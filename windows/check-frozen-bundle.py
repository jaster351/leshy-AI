"""Ранняя диагностика отсутствующих Python-модулей Лешего при сборке.

Запуск:
    python windows/check-frozen-bundle.py source
    python windows/check-frozen-bundle.py archive .autobuild/pyinstaller-work/Leshy/PYZ-00.toc .autobuild/package-dist/Leshy
Скрипт не изменяет исходники, установку и кэши сборки.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


REQUIRED_MODULES = (
    "leshy.network_blocking_runtime",
    "leshy.network_blocking_module",
    "leshy.provider_support",
    "leshy.private_support_bootstrap",
    "leshy.superagent_verified_ssh",
    "leshy.superagent_ssh_plan",
    "leshy.superagent_ssh_jobs",
    "leshy.kitesurf_cdp_adapter",
    "leshy.native_shell",
    "leshy.superagent_rdp_profile",
    "leshy.web_transport_settings",
    "leshy.web_certificates",
    "leshy.web_listeners",
    "leshy.web_transport_runtime",
    "leshy.web_acme",
    "leshy.web_local_ca",
    "leshy.web_readiness",
    "leshy.web_server_impl18",
)


def source_preflight(root: Path) -> None:
    # Разобрать каждый исходный Python-модуль до установки зависимостей и сборки
    # EXE: синтаксическая ошибка может выглядеть как отсутствие модуля в PyInstaller.
    verified = 0
    errors: list[str] = []
    for directory in ("src", "tests", "windows"):
        base = root / directory
        if not base.is_dir():
            continue
        for source_file in sorted(base.rglob("*.py")):
            if "__pycache__" in source_file.parts:
                continue
            try:
                compile(source_file.read_bytes(), str(source_file), "exec")
            except (SyntaxError, UnicodeError) as exc:
                line = getattr(exc, "lineno", None)
                message = getattr(exc, "msg", str(exc))
                errors.append(f"{source_file}:{line}: {message}")
                continue
            verified += 1
    if errors:
        raise RuntimeError(
            f"Ошибки синтаксиса Python в {len(errors)} исходниках:\n"
            + "\n".join(errors)
            + "\nИсправьте все указанные файлы перед сборкой."
        )
    print(f"BUILD PREFLIGHT OK: разобрано Python-исходников: {verified}.")

    source = root / "src"
    sys.path.insert(0, str(source))
    for module in REQUIRED_MODULES:
        expected = source.joinpath(*module.split(".")).with_suffix(".py").resolve()
        if not expected.is_file():
            raise RuntimeError(
                f"Локальный исходник отсутствует: {expected}. "
                "Проверьте git status, sparse-checkout и git ls-files для этого пути."
            )
        spec = importlib.util.find_spec(module)
        origin = Path(str(spec.origin or "")).resolve() if spec else None
        if origin != expected:
            raise RuntimeError(
                f"Неверный путь импорта {module}: {origin!s} (ожидается {expected}). "
                "Сборка может использовать устаревшую или дублирующую установку Python."
            )
        # Поиск модуля может найти синтаксически неверный исходник; проверяем
        # компиляцию без исполнения побочных действий до сборки EXE.
        compile(expected.read_bytes(), str(expected), "exec")
        print(f"BUILD PREFLIGHT OK: {module} -> {expected}")


def archive_preflight(path: Path, package_root: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"Манифест PyInstaller отсутствует: {path}")
    content = path.read_text(encoding="utf-8", errors="replace")
    repository_root = Path(__file__).resolve().parents[1]
    source_root = repository_root / "src" / "leshy"
    for module in REQUIRED_MODULES:
        if module in content:
            print(f"PYINSTALLER MANIFEST OK: {module} (скомпилированный PYZ)")
            continue
        filename = module.rsplit(".", 1)[-1] + ".py"
        expected = source_root / filename
        candidates = (
            package_root / "_internal" / "leshy" / filename,
            package_root / "leshy" / filename,
        )
        # Frozen-launcher может явно загрузить резервный исходник.
        if any(
            candidate.is_file() and candidate.read_bytes() == expected.read_bytes()
            for candidate in candidates
        ):
            print(f"PYINSTALLER MANIFEST OK: {module} (проверенный резервный исходник)")
            continue
        raise RuntimeError(
            f"PyInstaller не включил обязательный модуль {module}: "
            f"ни PYZ, ни неизменённый резервный исходник не найдены в {package_root}."
        )


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    mode = sys.argv[1] if len(sys.argv) > 1 else "source"
    if mode == "source":
        source_preflight(root)
    elif mode == "archive" and len(sys.argv) == 4:
        archive_preflight(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        raise SystemExit("Запуск: check-frozen-bundle.py source | archive <PYZ-00.toc> <package-root>")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"BUILD PREFLIGHT FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
