"""Переключатель доступа Лешего к ОС и отзыв запущенных им процессов."""
from __future__ import annotations
import os
import signal
import subprocess
import threading
import time
from .module_settings import module_enabled

DISABLED = "Управление операционной системой выключено в настройках; действие отменено."

class OSControlGate:
    def __init__(self, storage):
        self.storage = storage
        self.lock = threading.RLock()
        self.disabled = not module_enabled(storage, "os-control")
        self.generation = 0
        self.processes = {}
        self.active = 0
        # Подкласс контроллера может назначить callback для отзыва
        # ожидающих подтверждений при изменении настройки из другого процесса.
        self.on_disabled = None
        threading.Thread(target=self._watch, daemon=True, name="leshy-os-revocation").start()

    def enabled(self):
        try:
            value = module_enabled(self.storage, "os-control")
        except Exception:
            value = False
        with self.lock:
            return value and not self.disabled

    def check(self, generation=None):
        if not self.enabled():
            self.disable()
            raise PermissionError(DISABLED)
        with self.lock:
            if generation is not None and generation != self.generation:
                raise PermissionError(DISABLED)

    def begin(self):
        with self.lock:
            self.check()
            self.active += 1
            return self.generation

    def finish(self):
        with self.lock:
            self.active = max(0, self.active-1)

    def launch(self, argv, *, cwd=None, capture=False):
        with self.lock:
            self.check()
            kwargs = {"cwd": cwd, "stdout": subprocess.PIPE if capture else subprocess.DEVNULL,
                      "stderr": subprocess.PIPE if capture else subprocess.DEVNULL}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            else:
                kwargs["start_new_session"] = True
            proc = subprocess.Popen(argv, **kwargs)
            self.processes[proc.pid] = proc
            return proc

    def unregister(self, proc):
        with self.lock:
            self.processes.pop(proc.pid, None)

    @staticmethod
    def terminate(proc):
        if proc.poll() is not None:
            return True
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, check=False, timeout=5,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                os.killpg(proc.pid, signal.SIGKILL)
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        except (OSError, subprocess.TimeoutExpired):
            pass
        return proc.poll() is not None

    def disable(self):
        with self.lock:
            revoked_now = not self.disabled
            if revoked_now:
                self.generation += 1
            self.disabled = True
            procs = list(self.processes.values())
            active = self.active
            callback = self.on_disabled if revoked_now else None
        # Завершение контролируемых процессов и отзыв подтверждений должны
        # сработать при смене SQLite-настройки из любого процесса.
        stopped = sum(1 for proc in procs if self.terminate(proc))
        outcome = {"processes_stopped": stopped, "active_actions": active}
        if callable(callback):
            try:
                outcome.update(callback() or {})
            except Exception:
                # Не выдавать ложный успешный отчёт об отзыве подтверждений.
                outcome["approvals_cancel_failed"] = True
        with self.lock:
            for pid, proc in list(self.processes.items()):
                if proc.poll() is not None:
                    self.processes.pop(pid, None)
            outcome["processes_remaining"] = sum(
                p.poll() is None for p in self.processes.values()
            )
        return outcome

    def sync(self):
        try:
            permitted = module_enabled(self.storage, "os-control")
        except Exception:
            permitted = False
        if not permitted:
            return self.disable()
        with self.lock:
            if self.disabled:
                self.generation += 1
            self.disabled = False
            return {"processes_stopped": 0, "processes_remaining": 0, "active_actions": self.active}

    def status(self):
        with self.lock:
            active = self.active
            procs = sum(p.poll() is None for p in self.processes.values())
        return {"enabled": self.enabled(), "active_actions": active, "tracked_processes": procs}

    def _watch(self):
        while True:
            time.sleep(0.25)
            try:
                with self.lock:
                    for pid, proc in list(self.processes.items()):
                        if proc.poll() is not None:
                            self.processes.pop(pid, None)
                # Важно: когда остались только ожидающие подтверждения,
                # процессов уже нет, но смена switch всё ещё должна их отзывать.
                if not self.enabled():
                    self.disable()
            except Exception:
                pass
