"""Межпроцессный lock владельца SuperAgent на одном локальном SQLite-хранилище.

Блокировка удерживается открытым файловым дескриптором: ОС освобождает
её после завершения процесса. Lock-файл не удаляется, чтобы не допустить
двух владельцев из-за unlink/recreate. Для общей сетевой ФС этот
механизм не заменяет распределённый coordinator.
"""
from __future__ import annotations

import errno
import os
from pathlib import Path
import threading


class SuperAgentProcessLease:
    def __init__(self, database_path: Path | str):
        self.path = Path(str(Path(database_path).expanduser().resolve()) + ".superagent.lock")
        self._fd: int | None = None
        self._guard = threading.Lock()

    @property
    def held(self) -> bool:
        with self._guard:
            return self._fd is not None

    def try_acquire(self) -> bool:
        """Не блокирует: второй процесс остаётся наблюдателем."""
        with self._guard:
            if self._fd is not None:
                return True
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                os.close(fd)
                if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK,
                                 getattr(errno, "EWOULDBLOCK", errno.EAGAIN)}:
                    return False
                raise
            except BaseException:
                os.close(fd)
                raise
            self._fd = fd
            return True

    def release(self) -> None:
        with self._guard:
            fd = self._fd
            if fd is None:
                return
            self._fd = None
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


__all__ = ["SuperAgentProcessLease"]
