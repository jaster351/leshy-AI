from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import threading
import webbrowser


_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _install_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _brain_root() -> Path:
    """Единый каталог данных, всегда относительно Leshy.exe/корня запуска."""
    return (_install_root() / "LeshyBrain").resolve()


def _legacy_brain() -> Path:
    """Старое хранилище 1.10.4 и ниже — только для одноразовой миграции."""
    base = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return (base / "LeshyAI" / "LeshyBrain").resolve()


def _ps_quote(value: str) -> str:
    """Безопасная одинарная строка PowerShell."""
    return "'" + str(value).replace("'", "''") + "'"


def _cleanup_stale_llama_servers(brain_root: Path) -> None:
    """Удаляет старые llama-server только из runtime этого экземпляра Лешего.

    В 1.10.30 дочерний llama-server мог пережить закрытие Leshy.exe и продолжать
    держать vulkan-server.log/модель. При следующем запуске такой процесс больше
    не нужен и должен быть завершён до инициализации нового runtime.
    """
    if os.name != "nt":
        return
    runtime_root = str((brain_root / "runtime" / "llama.cpp").resolve())
    script = (
        "$root=" + _ps_quote(runtime_root) + ";"
        "Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" -ErrorAction SilentlyContinue | "
        "Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($root,[System.StringComparison]::OrdinalIgnoreCase) } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            creationflags=_CREATE_NO_WINDOW,
            check=False,
        )
    except Exception:
        # Очистка является страховкой. Ошибка здесь не должна блокировать запуск UI.
        pass


def _start_llama_watchdog(brain_root: Path) -> None:
    """Следит за Leshy.exe и закрывает только его дочерние llama-server.

    Watchdog — отдельный скрытый PowerShell-процесс. Поэтому он остаётся жив даже
    при аварийном завершении Python/PyInstaller и освобождает модель и log-файлы.
    Фильтр ParentProcessId не затрагивает llama-server других приложений или уже
    запущенного нового экземпляра Лешего.
    """
    if os.name != "nt":
        return
    parent_pid = os.getpid()
    runtime_root = str((brain_root / "runtime" / "llama.cpp").resolve())
    script = (
        "$parentId=" + str(parent_pid) + ";"
        "$root=" + _ps_quote(runtime_root) + ";"
        "try { $p=Get-Process -Id $parentId -ErrorAction SilentlyContinue; if($p){$p.WaitForExit()} } catch {};"
        "Start-Sleep -Milliseconds 250;"
        "Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" -ErrorAction SilentlyContinue | "
        "Where-Object { $_.ParentProcessId -eq $parentId -and $_.ExecutablePath -and $_.ExecutablePath.StartsWith($root,[System.StringComparison]::OrdinalIgnoreCase) } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception:
        # Основной close() runtime остаётся первым уровнем защиты.
        pass


def main() -> None:
    parser = argparse.ArgumentParser(prog="Leshy")
    parser.add_argument("--host", default=os.getenv("LESHY_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("LESHY_PORT", "8765")))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    install_root = _install_root()
    brain_root = _brain_root()
    legacy_brain = _legacy_brain()

    # Пользователь выбирает место установки. Все изменяемые данные Лешего
    # живут только относительно Leshy.exe: .\\LeshyBrain\\...
    os.environ["LESHY_INSTALL_ROOT"] = str(install_root)
    os.environ["LESHY_BRAIN_DIR"] = str(brain_root)

    # Без потери данных: старое AppData-хранилище копируется один раз в новую
    # относительную папку. После успешной проверки источник не удаляется.
    if legacy_brain.exists() and not brain_root.exists():
        os.environ["LESHY_LEGACY_BRAIN_DIR"] = str(legacy_brain)
    else:
        os.environ.pop("LESHY_LEGACY_BRAIN_DIR", None)

    os.environ["LESHY_HOST"] = args.host
    os.environ["LESHY_PORT"] = str(args.port)

    # Сначала освобождаем runtime/log-файлы, которые мог оставить старый процесс.
    _cleanup_stale_llama_servers(brain_root)
    # Затем создаём независимую страховку на весь срок жизни этого Leshy.exe.
    _start_llama_watchdog(brain_root)

    from leshy.diagnostic_publisher import DiagnosticPublisher
    from leshy.web_server import serve

    publisher = DiagnosticPublisher(brain_root)
    publisher.start()
    try:
        if not args.no_browser:
            threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}/")).start()
        serve(args.host, args.port)
    finally:
        publisher.stop()


if __name__ == "__main__":
    main()
