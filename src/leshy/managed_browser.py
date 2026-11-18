"""Управляемое окно Web UI, привязанное к процессу Windows Лешего.

Обычный webbrowser.open() не возвращает ID вкладки, поэтому нельзя
безопасно закрыть только вкладку Лешего. Вместо этого открывается
изолированное app-окно Edge/Chrome. Windows Job Object закрывает только
его процессы при нормальном завершении или аварийном исчезновении Leshy.exe.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from typing import Callable


_LOG = logging.getLogger(__name__)
_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


class _JobBasicLimit(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JobIoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimit),
        ("IoInfo", _JobIoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _BrowserJob:
    """Класс Job закрывает дочерние процессы, не затрагивая обычный браузер."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Job Object поддерживается только на Windows")
        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        dll.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        dll.CreateJobObjectW.restype = wintypes.HANDLE
        dll.SetInformationJobObject.argtypes = (
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
        )
        dll.SetInformationJobObject.restype = wintypes.BOOL
        dll.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        dll.AssignProcessToJobObject.restype = wintypes.BOOL
        dll.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        dll.OpenProcess.restype = wintypes.HANDLE
        dll.CloseHandle.argtypes = (wintypes.HANDLE,)
        dll.CloseHandle.restype = wintypes.BOOL
        self._dll = dll
        self._handle = dll.CreateJobObjectW(None, None)
        if not self._handle:
            raise OSError(ctypes.get_last_error(), "Не удалось создать Job Object")
        limits = _JobExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
        if not dll.SetInformationJobObject(
            self._handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            code = ctypes.get_last_error()
            self.close()
            raise OSError(code, "Нельзя включить автоматическое завершение окна")

    def attach(self, pid: int) -> None:
        handle = self._dll.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE,
            False,
            int(pid),
        )
        if not handle:
            raise OSError(ctypes.get_last_error(), "Нет доступа к процессу браузера")
        try:
            if not self._dll.AssignProcessToJobObject(self._handle, handle):
                raise OSError(
                    ctypes.get_last_error(), "Не удалось назначить браузер Job Object"
                )
        finally:
            self._dll.CloseHandle(handle)

    def close(self) -> None:
        handle = getattr(self, "_handle", None)
        if handle:
            self._handle = None
            self._dll.CloseHandle(handle)


def _browser_executable() -> Path | None:
    if os.name != "nt":
        return None
    roots = (
        os.environ.get("PROGRAMFILES(X86)", ""),
        os.environ.get("PROGRAMFILES", ""),
        os.environ.get("LOCALAPPDATA", ""),
    )
    # Edge стандартно установлен в Windows; Chrome — второй вариант.
    for subpath in (
        ("Microsoft", "Edge", "Application", "msedge.exe"),
        ("Google", "Chrome", "Application", "chrome.exe"),
    ):
        for root in roots:
            if root:
                candidate = Path(root).joinpath(*subpath)
                if candidate.is_file():
                    return candidate
    for executable in ("msedge.exe", "chrome.exe"):
        located = shutil.which(executable)
        if located:
            return Path(located)
    return None


class ManagedBrowser:
    """Не создавать дубли окна; не управлять существующими вкладками владельца."""

    def __init__(
        self,
        url: str,
        *,
        executable_finder: Callable[[], Path | None] = _browser_executable,
    ) -> None:
        self._url = url
        self._find_executable = executable_finder
        self._lock = threading.RLock()
        self._closed = False
        self._process: subprocess.Popen | None = None
        self._job: _BrowserJob | None = None
        self._profile: Path | None = None

    def open(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            if self._process is not None and self._process.poll() is None:
                return True  # Запущенное окно не дублируем.
            self._stop_locked()
            exe = self._find_executable()
            if exe is None:
                _LOG.warning("Edge/Chrome не найден: управляемое окно не открыто")
                return False
            job = None
            process = None
            profile = None
            try:
                job = _BrowserJob()
                profile = Path(tempfile.mkdtemp(prefix="leshy-ui-"))
                args = [
                    str(exe),
                    "--user-data-dir=" + str(profile),
                    "--app=" + self._url,
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-background-mode",
                ]
                process = subprocess.Popen(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                )
                # Без успешной привязки к Job не оставляем окно без хозяина.
                job.attach(process.pid)
                self._job = job
                self._process = process
                self._profile = profile
                return True
            except Exception:
                _LOG.exception("Не удалось создать управляемое окно браузера")
                if job is not None:
                    job.close()
                if process is not None and process.poll() is None:
                    try:
                        process.terminate()
                        process.wait(timeout=3)
                    except Exception:
                        _LOG.warning("Не удалось завершить неуправляемый запуск браузера")
                if profile is not None:
                    shutil.rmtree(profile, ignore_errors=True)
                return False

    def _stop_locked(self) -> None:
        job, proc, profile = self._job, self._process, self._profile
        self._job = None
        self._process = None
        self._profile = None
        if job is not None:
            job.close()
        if proc is not None:
            try:
                proc.wait(timeout=3)
            except (subprocess.TimeoutExpired, OSError):
                _LOG.warning("Окно браузера не завершилось за три секунды")
        if profile is not None:
            shutil.rmtree(profile, ignore_errors=True)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop_locked()
