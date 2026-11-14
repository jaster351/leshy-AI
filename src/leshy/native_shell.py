"""Resolve the OS's built-in command interpreter for Leshy OS Control.

No Docker, Guacamole, external agent, PTY service or user browser required.
Only a direct executable/argv is returned; process lifetime, owner approval,
revocation and output limits belong to the existing OSControlGate.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import shutil
from typing import Callable


@dataclass(frozen=True)
class NativeShell:
    kind: str
    executable: str
    prefix: tuple[str, ...]

    def argv(self, command: str) -> list[str]:
        if not isinstance(command, str) or not command.strip():
            raise ValueError("Введите команду для системной оболочки")
        if "\x00" in command or len(command.encode("utf-8")) > 4096:
            raise ValueError("Команда оболочки слишком длинная")
        return [self.executable, *self.prefix, command]


def resolve_native_shell(
    *, platform_name: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> NativeShell:
    """Platform-appropriate shell; fail closed instead of installing binaries.

    The preference order is intentionally explicit. On Windows, prefer native
    PowerShell; CMD is a compatibility fallback. Unix prefers Bash, then sh.
    """
    system = (platform_name or os.name).casefold()
    if system in {"nt", "windows"}:
        candidates = (
            ("powershell", "pwsh.exe", ("-NoProfile", "-NonInteractive", "-Command")),
            ("powershell", "powershell.exe", ("-NoProfile", "-NonInteractive", "-Command")),
            ("cmd", "cmd.exe", ("/D", "/S", "/C")),
        )
    elif system in {"posix", "linux", "darwin", "freebsd", "openbsd", "netbsd"}:
        candidates = (
            ("bash", "bash", ("--noprofile", "--norc", "-c")),
            ("sh", "sh", ("-c",)),
        )
    else:
        raise RuntimeError("Неподдерживаемая операционная система")
    for kind, candidate, prefix in candidates:
        executable = which(candidate)
        if executable:
            return NativeShell(kind, executable, prefix)
    raise RuntimeError("Встроенная системная оболочка не найдена")


__all__ = ["NativeShell", "resolve_native_shell"]
