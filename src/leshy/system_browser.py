from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import shutil
import subprocess

from .diagnostics import event, problem


_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@dataclass(frozen=True, slots=True)
class SystemBrowser:
    name: str
    executable: str


def _existing(path: Path | str | None) -> str:
    if not path:
        return ""
    value = Path(path).expanduser()
    try:
        if value.is_file():
            return str(value.resolve())
    except Exception:
        pass
    return ""


def _playwright_chromium(cache_root: Path) -> SystemBrowser | None:
    """На Linux Playwright кладёт Chromium в cache, а не в системный PATH.

    Используем обычный Chromium, не headless_shell: он подходит и для CDP
    Browser Use, и для Playwright. Не скачиваем браузер при диагностике.
    """
    try:
        releases = sorted(
            (item for item in cache_root.glob("chromium-*") if item.is_dir()),
            key=lambda item: item.name,
            reverse=True,
        )
        for release in releases:
            for relative in ("chrome-linux64/chrome", "chrome-linux/chrome"):
                binary = release / relative
                if binary.is_file() and os.access(binary, os.X_OK):
                    return SystemBrowser("Playwright Chromium", str(binary.resolve()))
    except (OSError, ValueError):
        pass
    return None


def _cached_playwright_browser() -> SystemBrowser | None:
    root = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if root and root != "0":
        folders = (Path(root).expanduser(),)
    else:
        folders = (Path.home() / ".cache" / "ms-playwright",)
    for folder in folders:
        browser = _playwright_chromium(folder)
        if browser is not None:
            return browser
    return None


def _candidate_browsers() -> tuple[SystemBrowser, ...]:
    candidates: list[SystemBrowser] = []

    override = _existing(os.getenv("LESHY_BROWSER_EXECUTABLE"))
    if override:
        candidates.append(SystemBrowser("Системный браузер", override))

    system = platform.system().lower()
    if system == "windows":
        program_files = os.getenv("PROGRAMFILES", "")
        program_files_x86 = os.getenv("PROGRAMFILES(X86)", "")
        local_app_data = os.getenv("LOCALAPPDATA", "")
        paths = (
            ("Microsoft Edge", Path(program_files_x86) / "Microsoft/Edge/Application/msedge.exe" if program_files_x86 else None),
            ("Microsoft Edge", Path(program_files) / "Microsoft/Edge/Application/msedge.exe" if program_files else None),
            ("Microsoft Edge", Path(local_app_data) / "Microsoft/Edge/Application/msedge.exe" if local_app_data else None),
            ("Google Chrome", Path(program_files) / "Google/Chrome/Application/chrome.exe" if program_files else None),
            ("Google Chrome", Path(program_files_x86) / "Google/Chrome/Application/chrome.exe" if program_files_x86 else None),
            ("Google Chrome", Path(local_app_data) / "Google/Chrome/Application/chrome.exe" if local_app_data else None),
            ("Chromium", Path(program_files) / "Chromium/Application/chrome.exe" if program_files else None),
            ("Chromium", Path(local_app_data) / "Chromium/Application/chrome.exe" if local_app_data else None),
        )
        for name, path in paths:
            executable = _existing(path)
            if executable:
                candidates.append(SystemBrowser(name, executable))
    elif system == "darwin":
        for name, path in (
            ("Microsoft Edge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            ("Google Chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ("Chromium", "/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ):
            executable = _existing(path)
            if executable:
                candidates.append(SystemBrowser(name, executable))
    else:
        for command, name in (
            ("microsoft-edge", "Microsoft Edge"),
            ("microsoft-edge-stable", "Microsoft Edge"),
            ("google-chrome", "Google Chrome"),
            ("google-chrome-stable", "Google Chrome"),
            ("chromium", "Chromium"),
            ("chromium-browser", "Chromium"),
        ):
            executable = shutil.which(command)
            if executable:
                candidates.append(SystemBrowser(name, executable))
        # Headless Linux нередко не имеет Chrome в PATH, но Playwright уже
        # установил свой Chromium в ~/.cache/ms-playwright.
        cached = _cached_playwright_browser()
        if cached is not None:
            candidates.append(cached)

    unique: list[SystemBrowser] = []
    seen: set[str] = set()
    for item in candidates:
        key = os.path.normcase(os.path.abspath(item.executable))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return tuple(unique)


def find_system_browser() -> SystemBrowser | None:
    candidates = _candidate_browsers()
    return candidates[0] if candidates else None


def _install_windows_browser() -> SystemBrowser | None:
    """Установить обычный системный Chromium-браузер, только если браузера нет.

    Сначала Microsoft Edge, затем Google Chrome. Установка выполняется через
    официальный системный пакетный менеджер winget. Никакой browser runtime не
    скачивается в каталог Лешего и не попадает в его дистрибутив.
    """
    if os.name != "nt":
        return None
    winget = shutil.which("winget")
    if not winget:
        problem("system_browser_install_unavailable", "winget не найден")
        return None

    for package_id, display_name in (
        ("Microsoft.Edge", "Microsoft Edge"),
        ("Google.Chrome", "Google Chrome"),
    ):
        event("system_browser_install_started", package_id=package_id, browser=display_name)
        try:
            completed = subprocess.run(
                [
                    winget,
                    "install",
                    "--id",
                    package_id,
                    "-e",
                    "--silent",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                    "--disable-interactivity",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=600,
                creationflags=_CREATE_NO_WINDOW,
                check=False,
            )
        except Exception as exc:
            problem("system_browser_install_failed", str(exc), package_id=package_id)
            continue

        browser = find_system_browser()
        if browser is not None:
            event(
                "system_browser_install_finished",
                package_id=package_id,
                browser=browser.name,
                exit_code=int(completed.returncode),
            )
            return browser
        problem(
            "system_browser_install_failed",
            f"После winget браузер не обнаружен, exit={completed.returncode}",
            package_id=package_id,
        )
    return None


def ensure_system_browser(*, auto_install: bool = True) -> SystemBrowser:
    browser = find_system_browser()
    if browser is not None:
        return browser
    if auto_install:
        browser = _install_windows_browser()
        if browser is not None:
            return browser
    raise RuntimeError(
        "Не найден Chrome/Chromium. На Ubuntu без графического интерфейса "
        "выполните: python -m playwright install --with-deps chromium. "
        "На Windows Леший может установить Edge/Chrome через winget."
    )


__all__ = ["SystemBrowser", "ensure_system_browser", "find_system_browser"]
