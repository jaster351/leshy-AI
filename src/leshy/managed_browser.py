"""Управляемое окно Web UI для штатного Windows Leshy.exe.

Обычный webbrowser.open не возвращает ID вкладки. Отдельное окно Edge/Chrome
в собственном профиле позволяет закрыть только интерфейс Лешего, а не
пользовательские вкладки. Windows Job Object автоматически завершает окно
и его дочерние процессы даже при принудительном завершении Leshy.exe.
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
_JOB_KILL_ON_CLOSE = 0x00002000
_JOB_EXTENDED_INFORMATION = 9
_CREATE_SUSPENDED = 0x00000004
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TOKEN_QUERY = 0x0008
_TOKEN_DUPLICATE = 0x0002
_MAXIMUM_ALLOWED = 0x02000000
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_STILL_ACTIVE = 259


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


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def _kernel32():
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    dll.CreateJobObjectW.restype = wintypes.HANDLE
    dll.SetInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
    )
    dll.SetInformationJobObject.restype = wintypes.BOOL
    dll.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    dll.AssignProcessToJobObject.restype = wintypes.BOOL
    dll.CreateProcessW.argtypes = (
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.POINTER(_StartupInfo), ctypes.POINTER(_ProcessInformation),
    )
    dll.CreateProcessW.restype = wintypes.BOOL
    dll.ResumeThread.argtypes = (wintypes.HANDLE,)
    dll.ResumeThread.restype = wintypes.DWORD
    dll.CloseHandle.argtypes = (wintypes.HANDLE,)
    dll.CloseHandle.restype = wintypes.BOOL
    dll.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    dll.WaitForSingleObject.restype = wintypes.DWORD
    dll.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    dll.GetExitCodeProcess.restype = wintypes.BOOL
    dll.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    dll.TerminateProcess.restype = wintypes.BOOL
    dll.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    dll.OpenProcess.restype = wintypes.HANDLE
    return dll


class _BrowserJob:
    """Закрывает процессы только выделенного браузера при потере дескриптора."""

    def __init__(self) -> None:
        self._dll = _kernel32()
        self._handle = self._dll.CreateJobObjectW(None, None)
        if not self._handle:
            raise OSError(ctypes.get_last_error(), "Не удалось создать Windows Job")
        info = _JobExtendedLimit()
        info.BasicLimitInformation.LimitFlags = _JOB_KILL_ON_CLOSE
        if not self._dll.SetInformationJobObject(
            self._handle, _JOB_EXTENDED_INFORMATION,
            ctypes.byref(info), ctypes.sizeof(info)
        ):
            code = ctypes.get_last_error()
            self.close()
            raise OSError(code, "Не удалось настроить удаление окна при выходе")

    def attach(self, handle: int) -> None:
        if not self._dll.AssignProcessToJobObject(self._handle, handle):
            raise OSError(ctypes.get_last_error(), "Браузер нельзя привязать к процессу Лешего")

    def close(self) -> None:
        if self._handle:
            handle, self._handle = self._handle, None
            self._dll.CloseHandle(handle)


class _WindowsBrowserProcess:
    """Запускаем процесс приостановленным, назначаем Job, потом возобновляем."""

    def __init__(self, exe: Path, args: list[str], job: _BrowserJob) -> None:
        self._dll = _kernel32()
        self._process: int | None = None
        self._thread: int | None = None
        self.pid = 0
        argv = subprocess.list2cmdline(args)
        command = ctypes.create_unicode_buffer(argv)
        startup = _StartupInfo()
        startup.cb = ctypes.sizeof(startup)
        result = _ProcessInformation()
        flags = _CREATE_SUSPENDED | _CREATE_UNICODE_ENVIRONMENT
        try:
            if _is_elevated():
                _spawn_as_desktop_user(exe, command, flags, startup, result)
            elif not self._dll.CreateProcessW(
                str(exe), command, None, None, False, flags,
                None, str(exe.parent), ctypes.byref(startup), ctypes.byref(result)
            ):
                raise OSError(ctypes.get_last_error(), "Не удалось запустить Edge/Chrome")
            self._process = result.hProcess
            self._thread = result.hThread
            self.pid = int(result.dwProcessId)
            # До назначения Job браузер не может создать собственные процессы.
            job.attach(self._process)
            if self._dll.ResumeThread(self._thread) == 0xFFFFFFFF:
                raise OSError(ctypes.get_last_error(), "Не удалось возобновить браузер")
            self._dll.CloseHandle(self._thread)
            self._thread = None
        except Exception:
            if self._process:
                self._dll.TerminateProcess(self._process, 1)
            elif result.hProcess:
                self._dll.TerminateProcess(result.hProcess, 1)
            if result.hThread and not self._thread:
                self._dll.CloseHandle(result.hThread)
            if result.hProcess and not self._process:
                self._dll.CloseHandle(result.hProcess)
            self.close()
            raise

    def poll(self) -> int | None:
        if not self._process:
            return 0
        value = wintypes.DWORD()
        if not self._dll.GetExitCodeProcess(self._process, ctypes.byref(value)):
            raise OSError(ctypes.get_last_error(), "Не удалось проверить браузер")
        return None if value.value == _STILL_ACTIVE else int(value.value)

    def wait(self, timeout: float = 3) -> int | None:
        if not self._process:
            return 0
        result = self._dll.WaitForSingleObject(self._process, int(timeout * 1000))
        if result == _WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired("управляемый Edge/Chrome", timeout)
        if result != _WAIT_OBJECT_0:
            raise OSError(ctypes.get_last_error(), "Ошибка ожидания браузера")
        return self.poll()

    def terminate(self) -> None:
        if self._process:
            self._dll.TerminateProcess(self._process, 1)

    def close(self) -> None:
        if self._thread:
            self._dll.CloseHandle(self._thread)
            self._thread = None
        if self._process:
            self._dll.CloseHandle(self._process)
            self._process = None


def _is_elevated() -> bool:
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.IsUserAnAdmin.argtypes = ()
    shell.IsUserAnAdmin.restype = wintypes.BOOL
    return bool(shell.IsUserAnAdmin())


def _spawn_as_desktop_user(
    exe: Path, command, flags: int, startup: _StartupInfo, result: _ProcessInformation
) -> None:
    """Из повышенного Leshy.exe создаём окно с правами пользователя Explorer."""

    kernel = _kernel32()
    user = ctypes.WinDLL("user32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    user.GetShellWindow.argtypes = ()
    user.GetShellWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    security.OpenProcessToken.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
    )
    security.OpenProcessToken.restype = wintypes.BOOL
    security.GetTokenInformation.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    security.GetTokenInformation.restype = wintypes.BOOL
    security.DuplicateTokenEx.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, ctypes.c_int,
        ctypes.c_int, ctypes.POINTER(wintypes.HANDLE),
    )
    security.DuplicateTokenEx.restype = wintypes.BOOL
    security.CreateProcessWithTokenW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPWSTR,
        wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.POINTER(_StartupInfo), ctypes.POINTER(_ProcessInformation),
    )
    security.CreateProcessWithTokenW.restype = wintypes.BOOL

    window = user.GetShellWindow()
    if not window:
        raise OSError("Не найден рабочий стол Explorer для запуска браузера без UAC")
    shell_pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(window, ctypes.byref(shell_pid))
    if not shell_pid.value:
        raise OSError("Не удалось определить PID оболочки Windows")
    shell_process = kernel.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, shell_pid.value)
    if not shell_process:
        raise OSError(ctypes.get_last_error(), "Нет доступа к оболочке рабочего стола")
    token = wintypes.HANDLE()
    primary = wintypes.HANDLE()
    try:
        if not security.OpenProcessToken(shell_process, _TOKEN_QUERY | _TOKEN_DUPLICATE, ctypes.byref(token)):
            raise OSError(ctypes.get_last_error(), "Нет доступа к токену Explorer")
        elevated = wintypes.DWORD()
        size = wintypes.DWORD()
        if not security.GetTokenInformation(
            token, 20, ctypes.byref(elevated), ctypes.sizeof(elevated), ctypes.byref(size)
        ):
            raise OSError(ctypes.get_last_error(), "Не удалось проверить права Explorer")
        if elevated.value:
            raise PermissionError("Рабочий стол Explorer повышен: браузер нельзя безопасно открыть")
        if not security.DuplicateTokenEx(
            token, _MAXIMUM_ALLOWED, None, 2, 1, ctypes.byref(primary)
        ):
            raise OSError(ctypes.get_last_error(), "Не удалось получить пользовательский токен")
        if not security.CreateProcessWithTokenW(
            primary, 0, str(exe), command, flags, None, str(exe.parent),
            ctypes.byref(startup), ctypes.byref(result)
        ):
            raise OSError(ctypes.get_last_error(), "Не удалось запустить браузер от пользователя")
    finally:
        if primary:
            kernel.CloseHandle(primary)
        if token:
            kernel.CloseHandle(token)
        kernel.CloseHandle(shell_process)


def _browser_executable() -> Path | None:
    if os.name != "nt":
        return None
    roots = (
        os.environ.get("PROGRAMFILES(X86)", ""),
        os.environ.get("PROGRAMFILES", ""),
        os.environ.get("LOCALAPPDATA", ""),
    )
    for segments in (
        ("Microsoft", "Edge", "Application", "msedge.exe"),
        ("Google", "Chrome", "Application", "chrome.exe"),
    ):
        for root in roots:
            if root:
                candidate = Path(root).joinpath(*segments)
                if candidate.is_file():
                    return candidate
    for executable in ("msedge.exe", "chrome.exe"):
        located = shutil.which(executable)
        if located:
            return Path(located)
    return None


class ManagedBrowser:
    """Один браузер для Leshy.exe: никаких общих пользовательских вкладок."""

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
        self._process: _WindowsBrowserProcess | None = None
        self._job: _BrowserJob | None = None
        self._profile: Path | None = None

    def open(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            if self._process is not None and self._process.poll() is None:
                return True  # Уже есть окно: не открываем десятки вкладок.
            self._stop_locked()
            executable = self._find_executable()
            if executable is None:
                _LOG.warning("Для управляемого Web UI нужен установленный Edge или Chrome")
                return False
            profile = None
            job = None
            process = None
            try:
                job = _BrowserJob()
                profile = Path(tempfile.mkdtemp(prefix="leshy-ui-"))
                args = [
                    str(executable),
                    "--user-data-dir=" + str(profile),
                    "--app=" + self._url,
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-background-mode",
                ]
                process = _WindowsBrowserProcess(executable, args, job)
                self._job = job
                self._process = process
                self._profile = profile
                return True
            except Exception:
                _LOG.exception("Управляемое окно браузера не открылось; чужие вкладки не затронуты")
                if job is not None:
                    job.close()
                if process is not None:
                    process.terminate()
                    process.close()
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
                _LOG.warning("Процесс окна Web UI не завершился за три секунды")
            finally:
                proc.close()
        if profile is not None:
            shutil.rmtree(profile, ignore_errors=True)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop_locked()
