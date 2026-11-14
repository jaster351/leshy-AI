from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import time
import threading
from types import MethodType
from typing import Any

from .diagnostics import event, problem
from .llm import ChatMessage
from .storage import utcnow
from .os_control_guard import OSControlGate, DISABLED
from .native_shell import resolve_native_shell
from .action_safety_guard import check_external_action


@dataclass(slots=True)
class OSActionResult:
    action: str
    ok: bool
    summary: str
    data: Any = None


class OperatingSystemController:
    """Локальные действия с ОС по явной просьбе пользователя.

    Инструмент не является автономным администратором: действие выполняется только
    в ответ на пользовательское сообщение, распознанное как просьба работать с
    локальной ОС. Команды выполняются с правами процесса Лешего; UAC не обходится.
    """

    _INTENT_RE = re.compile(
        r"(?i)(?:^\s*/os\b|\b(?:windows|виндовс|операционн\w*\s+систем\w*|на\s+компьютер\w*|на\s+пк)\b|"
        r"\b(?:открой\s+(?:файл|папку|программ)|запусти\s+(?:программ|приложени|процесс|скрипт)|выполни\s+(?:команду|скрипт|powershell)|создай\s+папк|прочитай\s+файл|покажи\s+(?:файлы|папки|процессы)|"
        r"останови\s+процесс|закрой\s+программ|скопируй\s+файл|перемести\s+файл|удали\s+файл)\b)"
    )
    _DANGEROUS_RE = re.compile(
        r"(?i)(?:format(?:-volume)?\b|clear-disk\b|remove-partition\b|diskpart\b|"
        r"bcdedit\b|bootrec\b|cipher\s+/w\b|remove-item\s+[^\r\n]*(?:windows|system32)[^\r\n]*-recurse|"
        r"reg(?:\.exe)?\s+delete\s+HKLM\\|set-mppreference\b[^\r\n]*disable|"
        r"disable-windowsoptionalfeature\b|manage-bde\b[^\r\n]*-off)"
    )

    _NATIVE_CRITICAL_RE = re.compile(
        r"(?i)(?:\b(?:mkfs(?:\.[a-z0-9]+)?|wipefs|sfdisk|fdisk|cfdisk|"
        r"parted|shred|blkdiscard)\b|"
        r"\bdd\b[^\r\n]*\bof\s*=\s*/dev/|"
        r"\bsystemctl\s+(?:poweroff|halt|reboot)\b|"
        r"\b(?:poweroff|halt|shutdown|reboot)\b)"
    )

    def __init__(self, app):
        self.app = app
        self.storage = app.storage
        self.brain_dir = Path(app.settings.brain_dir)
        self.home = Path.home()
        self._last_action: dict[str, Any] | None = None
        self._gate = OSControlGate(self.storage)
        self._local = threading.local()

    def enabled(self):
        return self._gate.enabled()

    def sync_enabled(self):
        return self._gate.sync()

    def assert_allowed(self):
        self._gate.check()

    def _authorize_owner(self, owner_id: str) -> None:
        """Telegram OS privileges are owner-only even for direct calls.

        Сеть/API и Telegram могут передавать owner_id в разные маршруты;
        факт, что строка начинается с telegram:, НЕ подтверждает роль.
        """
        identity = str(owner_id or "").strip()
        if identity.startswith("telegram:"):
            identifier = identity.partition(":")[2]
            telegram = getattr(self.app, "telegram", None)
            check = getattr(telegram, "_is_owner", None)
            if not identifier or not callable(check) or not check(identifier):
                raise PermissionError(
                    "Управление ОС доступно только подтверждённому хозяину Telegram."
                )

    def is_request(self, message: str) -> bool:
        return bool(self._INTENT_RE.search(str(message or "")))

    @staticmethod
    def _clip(value: str, limit: int = 12000) -> str:
        text = str(value or "")
        return text if len(text) <= limit else text[:limit] + "\n…[обрезано]"

    def _audit(self, action: str, args: dict[str, Any], result: OSActionResult, *, owner_id: str) -> None:
        safe_args = dict(args)
        if "content" in safe_args:
            safe_args["content"] = f"<{len(str(safe_args['content']))} символов>"
        if action in {"powershell", "native_shell"} and "command" in safe_args:
            raw = str(safe_args["command"])
            safe_args["command"] = {
                "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                "bytes": len(raw.encode("utf-8")),
            }
        payload = {
            "action": action,
            "owner_id": str(owner_id)[:200],
            "ok": bool(result.ok),
            "summary": result.summary[:500],
            "arguments": safe_args,
            "created_at": utcnow(),
        }
        self.storage.audit("os.action", payload)
        self._last_action = payload

    @staticmethod
    def _path(value: Any) -> Path:
        text = os.path.expandvars(os.path.expanduser(str(value or "").strip().strip('"')))
        if not text:
            raise ValueError("Не указан путь")
        return Path(text)

    @staticmethod
    def _is_root_or_system(path: Path) -> bool:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path.absolute()
        forbidden = {Path("C:/").resolve() if os.name == "nt" else Path("/").resolve()}
        windir = os.getenv("WINDIR")
        if windir:
            forbidden.add(Path(windir).resolve())
            forbidden.add((Path(windir) / "System32").resolve())
        return resolved in forbidden

    def _powershell(self, command: str, *, timeout: int = 60) -> str:
        command = str(command or "").strip()
        if not command:
            raise ValueError("PowerShell-команда пустая")
        if self._DANGEROUS_RE.search(command):
            raise PermissionError("Критические системные команды запрещены")
        exe = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or shutil.which("powershell") or shutil.which("pwsh")
        if not exe:
            raise RuntimeError("PowerShell не найден")
        generation = getattr(self._local, "generation", None)
        self._gate.check(generation)
        proc = self._gate.launch([exe, "-NoProfile", "-NonInteractive", "-Command", command], capture=True)
        deadline = time.monotonic() + max(1, min(int(timeout), 180))
        try:
            while True:
                self._gate.check(generation)
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError("Истекло время PowerShell-команды")
                try:
                    out, err = proc.communicate(timeout=min(0.25, left))
                    break
                except subprocess.TimeoutExpired:
                    pass
            self._gate.check(generation)
            output = (out or b"").decode("utf-8", errors="replace")
            errors = (err or b"").decode("utf-8", errors="replace")
            if proc.returncode != 0:
                raise RuntimeError(f"PowerShell завершился с кодом {proc.returncode}; полный вывод команды не сохраняется в лог")
            return self._clip(output + (chr(10)+errors if errors else ""))
        finally:
            if proc.poll() is None:
                self._gate.terminate(proc)
            self._gate.unregister(proc)

    def _native_shell(self, command: str, *, timeout: int = 60) -> tuple[str, str]:
        """Execute through an installed OS shell using the revocable gate.

        Each invocation starts a fresh OS process, not a persistent PTY.
        """
        shell = resolve_native_shell()
        if self._DANGEROUS_RE.search(command) or self._NATIVE_CRITICAL_RE.search(command):
            raise PermissionError("Критическая системная команда запрещена")
        argv = shell.argv(command)
        generation = getattr(self._local, "generation", None)
        self._gate.check(generation)
        proc = self._gate.launch(argv, capture=True)
        deadline = time.monotonic() + max(2, min(int(timeout), 120))
        try:
            while True:
                self._gate.check(generation)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Истекло время системной команды")
                try:
                    stdout, stderr = proc.communicate(timeout=min(0.2, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            self._gate.check(generation)
            out = (stdout or b"").decode("utf-8", errors="replace")
            err = (stderr or b"").decode("utf-8", errors="replace")
            if proc.returncode != 0:
                raise RuntimeError(
                    f"{shell.kind} завершился с кодом {proc.returncode}: "
                    + " подробный вывод не сохраняется в лог"
                )
            return shell.kind, self._clip(out + ("\n" + err if err else ""))
        finally:
            if proc.poll() is None:
                self._gate.terminate(proc)
            self._gate.unregister(proc)

    def execute(self, action: str, args: dict[str, Any] | None = None, *, owner_id: str = "web:local") -> OSActionResult:
        action = str(action or "").strip().lower()
        args = dict(args or {})
        started = time.monotonic()
        generation = None
        try:
            self._authorize_owner(owner_id)
            # Независимый запрет действует даже при пользовательском подтверждении.
            check_external_action(action, args)
            generation = self._gate.begin()
            self._local.generation = generation
            if action == "system_status":
                root = Path(os.getenv("SystemDrive", "C:")) / "\\" if os.name == "nt" else Path("/")
                usage = shutil.disk_usage(root)
                data = {
                    "os": platform.platform(),
                    "hostname": socket.gethostname(),
                    "user": os.getenv("USERNAME") or os.getenv("USER") or "",
                    "cwd": os.getcwd(),
                    "home": str(self.home),
                    "disk_total_gb": round(usage.total / 1024**3, 1),
                    "disk_free_gb": round(usage.free / 1024**3, 1),
                }
                result = OSActionResult(action, True, "Состояние операционной системы получено", data)

            elif action == "list_dir":
                path = self._path(args.get("path") or self.home)
                limit = max(1, min(int(args.get("limit") or 200), 1000))
                rows = []
                for item in list(path.iterdir())[:limit]:
                    try:
                        stat = item.stat()
                        rows.append({"name": item.name, "type": "dir" if item.is_dir() else "file", "size": stat.st_size})
                    except OSError:
                        rows.append({"name": item.name, "type": "unknown", "size": None})
                result = OSActionResult(action, True, f"Показано объектов: {len(rows)} · {path}", rows)

            elif action == "read_text":
                path = self._path(args.get("path"))
                max_bytes = max(1024, min(int(args.get("max_bytes") or 1024 * 1024), 4 * 1024 * 1024))
                self._gate.check(generation)
                with path.open("rb") as source:
                    raw = source.read(max_bytes)
                self._gate.check(generation)
                text = raw.decode(str(args.get("encoding") or "utf-8"), errors="replace")
                result = OSActionResult(action, True, f"Файл прочитан: {path}", self._clip(text))

            elif action == "write_text":
                path = self._path(args.get("path"))
                content = str(args.get("content") or "")
                if len(content.encode("utf-8")) > 4 * 1024 * 1024:
                    raise ValueError("За один запрос можно записать не более 4 МБ текста")
                path.parent.mkdir(parents=True, exist_ok=True)
                mode = "a" if bool(args.get("append")) else "w"
                with path.open(mode, encoding=str(args.get("encoding") or "utf-8")) as handle:
                    for pos in range(0, len(content), 32768):
                        self._gate.check(generation)
                        handle.write(content[pos:pos+32768])
                        handle.flush()
                        self._gate.check(generation)
                result = OSActionResult(action, True, f"Файл {'дополнен' if mode == 'a' else 'записан'}: {path}")

            elif action == "make_dir":
                path = self._path(args.get("path")); path.mkdir(parents=True, exist_ok=True)
                result = OSActionResult(action, True, f"Папка готова: {path}")

            elif action in {"copy", "move"}:
                src = self._path(args.get("source"))
                dst = self._path(args.get("destination"))
                self._gate.check(generation)
                dst.parent.mkdir(parents=True, exist_ok=True)
                if action == "copy":
                    if src.is_dir():
                        raise PermissionError("Копирование каталогов пока запрещено: безопасное прерывание не реализовано")
                    # Порционный перенос данных: при выключении прекращаем запись.
                    with src.open("rb") as source, dst.open("xb") as target:
                        while True:
                            self._gate.check(generation)
                            block = source.read(65536)
                            if not block:
                                break
                            target.write(block)
                            self._gate.check(generation)
                    verb = "Скопировано"
                else:
                    # os.replace не переходит к неконтролируемому shutil.copytree
                    # между томами; ошибки EXDEV оставляются без побочных копий.
                    if dst.exists() or dst.is_symlink():
                        raise FileExistsError("Назначение уже существует; перезапись запрещена")
                    self._gate.check(generation)
                    src.rename(dst)
                    verb = "Перемещено"
                self._gate.check(generation)
                result = OSActionResult(action, True, f"{verb}: {src} → {dst}")

            elif action == "delete":
                path = self._path(args.get("path"))
                if self._is_root_or_system(path):
                    raise PermissionError("Корень диска и системные каталоги нельзя удалить через чат")
                if path.is_dir():
                    if bool(args.get("recursive")):
                        raise PermissionError("Рекурсивное удаление отключено до реализации контролируемого поэтапного удаления")
                    self._gate.check(generation)
                    path.rmdir()
                else:
                    self._gate.check(generation)
                    path.unlink()
                result = OSActionResult(action, True, f"Удалено: {path}")

            elif action == "process_list":
                if os.name == "nt":
                    output = self._powershell("Get-Process | Sort-Object CPU -Descending | Select-Object -First 80 Id,ProcessName,CPU,WorkingSet64 | ConvertTo-Json -Compress")
                    try: data = json.loads(output) if output else []
                    except json.JSONDecodeError: data = output
                else:
                    _, output = self._native_shell("ps -eo pid,ppid,comm")
                    data = output
                result = OSActionResult(action, True, "Список процессов получен", data)

            elif action == "start_process":
                program = str(args.get("program") or "").strip()
                if not program: raise ValueError("Не указана программа")
                proc_args = args.get("args") or []
                if isinstance(proc_args, str): proc_args = [proc_args]
                proc = self._gate.launch([program, *[str(x) for x in proc_args]], cwd=str(args.get("cwd") or self.home))
                result = OSActionResult(action, True, f"Программа запущена: {program} · PID {proc.pid}", {"pid": proc.pid})

            elif action == "stop_process":
                pid = int(args.get("pid") or 0)
                if pid <= 0: raise ValueError("Нужен PID процесса")
                if os.name == "nt":
                    task = self._gate.launch(["taskkill", "/PID", str(pid), "/T", "/F"], capture=True)
                    try:
                        while True:
                            self._gate.check(generation)
                            try:
                                task.communicate(timeout=0.25)
                                break
                            except subprocess.TimeoutExpired:
                                continue
                        if task.returncode != 0:
                            raise RuntimeError("Не удалось остановить указанный процесс")
                    finally:
                        if task.poll() is None:
                            self._gate.terminate(task)
                        self._gate.unregister(task)
                else:
                    self._gate.check(generation)
                    os.kill(pid, 15)
                result = OSActionResult(action, True, f"Процесс остановлен: PID {pid}")

            elif action == "open_path":
                value = str(args.get("path") or args.get("target") or "").strip()
                if not value: raise ValueError("Не указан путь/адрес")
                raise PermissionError("Нельзя открывать внешние обработчики файлов: их процессы не отслеживаются для остановки.")

            elif action == "powershell":
                output = self._powershell(str(args.get("command") or ""), timeout=int(args.get("timeout") or 60))
                result = OSActionResult(action, True, "PowerShell-команда выполнена", output)

            elif action == "native_shell":
                kind, output = self._native_shell(
                    str(args.get("command") or ""),
                    timeout=int(args.get("timeout") or 60),
                )
                result = OSActionResult(action, True, f"Команда выполнена через {kind}", output)

            else:
                raise ValueError(f"Неизвестное действие ОС: {action}")
            self._gate.check(generation)
        except Exception as exc:
            result = OSActionResult(action, False, str(exc), None)
            # Shell exception text can include stdout/stderr and secrets.
            problem("os_action_failed",
                    type(exc).__name__ if action in {"native_shell", "powershell"} else str(exc),
                    action=action, owner_id=str(owner_id)[:200])
        finally:
            self._local.generation = None
            if generation is not None:
                self._gate.finish()
        self._audit(action, args, result, owner_id=owner_id)
        event("os_action_finished", action=action, ok=result.ok, latency_ms=int((time.monotonic() - started) * 1000))
        return result

    @staticmethod
    def _direct_command(message: str) -> tuple[str, dict[str, Any]] | None:
        text = str(message or "").strip()
        if not text.lower().startswith("/os"):
            return None
        rest = text[3:].strip()
        if not rest or rest.lower() in {"help", "помощь"}:
            return "help", {}
        cmd, _, tail = rest.partition(" ")
        cmd = cmd.lower(); tail = tail.strip()
        mapping = {
            "status": ("system_status", {}), "статус": ("system_status", {}),
            "ps": ("process_list", {}), "processes": ("process_list", {}), "процессы": ("process_list", {}),
            "ls": ("list_dir", {"path": tail or str(Path.home())}), "dir": ("list_dir", {"path": tail or str(Path.home())}),
            "cat": ("read_text", {"path": tail}), "read": ("read_text", {"path": tail}),
            "mkdir": ("make_dir", {"path": tail}), "open": ("open_path", {"path": tail}),
            "run": ("native_shell", {"command": tail}),
            "shell": ("native_shell", {"command": tail}),
            "powershell": ("powershell", {"command": tail}),
        }
        return mapping.get(cmd)

    def _plan_natural(self, message: str) -> tuple[str, dict[str, Any]]:
        self.assert_allowed()
        prompt = [
            ChatMessage("system", "/no_think\nТы планировщик локального инструмента Лешего под ОС " + platform.system() + ". Верни ТОЛЬКО JSON без markdown. "
                        "Схема: {\"action\":\"...\",\"args\":{...}}. Разрешённые action: system_status,list_dir,read_text,write_text,make_dir,copy,move,delete,process_list,start_process,stop_process,open_path,powershell,native_shell. "
                        "Выбирай native_shell только когда пользователь явно просит выполнить команду/скрипт или задачу невозможно выразить другим action. Не придумывай пути, PID и имена программ, которых нет в запросе; если данных недостаточно, верни {\"action\":\"need_clarification\",\"args\":{\"question\":\"...\"}}."),
            ChatMessage("user", str(message)),
        ]
        raw = str(self.app.llm.chat(prompt) or "").strip()
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            raise ValueError("Не удалось построить безопасный план системного действия")
        plan = json.loads(match.group(0))
        action = str(plan.get("action") or "").strip()
        args = plan.get("args") if isinstance(plan.get("args"), dict) else {}
        return action, args

    def handle_user_request(self, message: str, *, owner_id: str = "web:local", planned: tuple[str, dict[str, Any]] | None = None) -> str | None:
        if not self.is_request(message):
            return None
        self._authorize_owner(owner_id)
        if not self.enabled():
            return DISABLED
        action, args = planned if planned is not None else (self._direct_command(message) or self._plan_natural(message))

        if action == "help":
            return (
                "Управление ОС включено. Можно просить естественным языком или использовать `/os`: "
                "`/os status`, `/os ps`, `/os ls ПУТЬ`, `/os cat ФАЙЛ`, `/os mkdir ПУТЬ`, "
                "`/os open ПУТЬ`, `/os run КОМАНДА` (PowerShell/CMD/Bash/sh в зависимости от ОС)."
            )
        if action == "need_clarification":
            return str(args.get("question") or "Уточните системное действие.")

        result = self.execute(action, args, owner_id=owner_id)
        if not result.ok:
            return "Не удалось выполнить действие в ОС: " + result.summary
        if result.data in (None, "", [], {}):
            return result.summary
        rendered = result.data if isinstance(result.data, str) else json.dumps(result.data, ensure_ascii=False, indent=2)
        return result.summary + "\n\n" + self._clip(rendered)

    def status(self) -> dict[str, Any]:
        try:
            shell = resolve_native_shell().kind
        except RuntimeError:
            shell = "unavailable"
        return {
            **self._gate.status(),
            "platform": platform.system(),
            "native_shell": shell,
            "runs_with_process_privileges": True,
            "uac_bypass": False,
            "natural_language_requests": True,
            "direct_command_prefix": "/os",
            "last_action": self._last_action,
        }


def install_os_control(app) -> OperatingSystemController:
    existing = getattr(app, "os_control", None)
    if isinstance(existing, OperatingSystemController):
        return existing
    controller = OperatingSystemController(app)
    app.os_control = controller
    original_chat = app.chat

    def chat_with_os(self, message: str, *, owner_id: str = "web:local", session_id: str | None = None):
        text = str(message or "").strip()
        if controller.is_request(text):
            if not controller.enabled():
                sid = self._ensure_session(owner_id, session_id)
                return {"session_id": sid, "answer": DISABLED, "created_at": self._save_assistant(sid, DISABLED), "sources": [], "os_action": True}
            # OS intent matching is intentionally broad (e.g. "открой"). The LLM
            # may correctly select a web or browser action. Never send such an
            # action to OperatingSystemController.execute: route it back through
            # the ordinary chat/search pipeline instead.
            try:
                planned = controller._direct_command(text) or controller._plan_natural(text)
            except (ValueError, TypeError, json.JSONDecodeError):
                return original_chat(message, owner_id=owner_id, session_id=session_id)
            supported = {
                "help", "need_clarification", "system_status", "list_dir",
                "read_text", "write_text", "make_dir", "copy", "move",
                "delete", "process_list", "start_process", "stop_process",
                "open_path", "powershell", "native_shell",
            }
            if planned[0] not in supported:
                event("os_action_delegated_to_chat", action=str(planned[0])[:80], owner_id=owner_id)
                return original_chat(message, owner_id=owner_id, session_id=session_id)
            sid = self._ensure_session(owner_id, session_id)
            now = utcnow()
            with self.storage.connection() as conn:
                conn.execute("INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)", (sid, "user", text, now))
                count = int(conn.execute("SELECT COUNT(*) AS c FROM messages WHERE session_id=?", (sid,)).fetchone()["c"])
                if count == 1:
                    conn.execute("UPDATE sessions SET title=?,title_source=?,updated_at=? WHERE id=?", (" ".join(text.split())[:80], "auto", now, sid))
            answer = controller.handle_user_request(text, owner_id=owner_id, planned=planned) or "Системное действие не распознано."
            return {"session_id": sid, "answer": answer, "created_at": self._save_assistant(sid, answer), "sources": [], "os_action": True}
        return original_chat(message, owner_id=owner_id, session_id=session_id)

    app.chat = MethodType(chat_with_os, app)
    return controller


__all__ = ["OperatingSystemController", "OSActionResult", "install_os_control"]
