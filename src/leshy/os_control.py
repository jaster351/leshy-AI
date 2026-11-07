from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import time
from types import MethodType
from typing import Any

from .diagnostics import event, problem
from .llm import ChatMessage
from .storage import utcnow


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

    def __init__(self, app):
        self.app = app
        self.storage = app.storage
        self.brain_dir = Path(app.settings.brain_dir)
        self.home = Path.home()
        self._last_action: dict[str, Any] | None = None

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
            raise PermissionError(
                "Команда затрагивает критические системные/дисковые настройки. "
                "Леший не выполняет такие команды автоматически из чата."
            )
        exe = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or shutil.which("powershell") or shutil.which("pwsh")
        if not exe:
            raise RuntimeError("PowerShell не найден")
        proc = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=max(1, min(int(timeout), 180)),
            creationflags=(0x08000000 if os.name == "nt" else 0),
        )
        output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        if proc.returncode != 0:
            raise RuntimeError(f"PowerShell exit {proc.returncode}: {self._clip(output, 4000)}")
        return self._clip(output.strip())

    def execute(self, action: str, args: dict[str, Any] | None = None, *, owner_id: str = "web:local") -> OSActionResult:
        action = str(action or "").strip().lower()
        args = dict(args or {})
        started = time.monotonic()
        try:
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
                raw = path.read_bytes()[:max_bytes]
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
                    handle.write(content)
                result = OSActionResult(action, True, f"Файл {'дополнен' if mode == 'a' else 'записан'}: {path}")

            elif action == "make_dir":
                path = self._path(args.get("path")); path.mkdir(parents=True, exist_ok=True)
                result = OSActionResult(action, True, f"Папка готова: {path}")

            elif action in {"copy", "move"}:
                src = self._path(args.get("source")); dst = self._path(args.get("destination"))
                dst.parent.mkdir(parents=True, exist_ok=True)
                if action == "copy":
                    if src.is_dir(): shutil.copytree(src, dst, dirs_exist_ok=True)
                    else: shutil.copy2(src, dst)
                    verb = "Скопировано"
                else:
                    shutil.move(str(src), str(dst)); verb = "Перемещено"
                result = OSActionResult(action, True, f"{verb}: {src} → {dst}")

            elif action == "delete":
                path = self._path(args.get("path"))
                if self._is_root_or_system(path):
                    raise PermissionError("Корень диска и системные каталоги нельзя удалить через чат")
                if path.is_dir():
                    if bool(args.get("recursive")):
                        shutil.rmtree(path)
                    else:
                        path.rmdir()
                else:
                    path.unlink()
                result = OSActionResult(action, True, f"Удалено: {path}")

            elif action == "process_list":
                output = self._powershell("Get-Process | Sort-Object CPU -Descending | Select-Object -First 80 Id,ProcessName,CPU,WorkingSet64 | ConvertTo-Json -Compress")
                try: data = json.loads(output) if output else []
                except json.JSONDecodeError: data = output
                result = OSActionResult(action, True, "Список процессов получен", data)

            elif action == "start_process":
                program = str(args.get("program") or "").strip()
                if not program: raise ValueError("Не указана программа")
                proc_args = args.get("args") or []
                if isinstance(proc_args, str): proc_args = [proc_args]
                proc = subprocess.Popen([program, *[str(x) for x in proc_args]], cwd=str(args.get("cwd") or self.home))
                result = OSActionResult(action, True, f"Программа запущена: {program} · PID {proc.pid}", {"pid": proc.pid})

            elif action == "stop_process":
                pid = int(args.get("pid") or 0)
                if pid <= 0: raise ValueError("Нужен PID процесса")
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=True, capture_output=True, text=True, creationflags=0x08000000)
                else:
                    os.kill(pid, 15)
                result = OSActionResult(action, True, f"Процесс остановлен: PID {pid}")

            elif action == "open_path":
                value = str(args.get("path") or args.get("target") or "").strip()
                if not value: raise ValueError("Не указан путь/адрес")
                if os.name == "nt": os.startfile(value)  # type: ignore[attr-defined]
                else: subprocess.Popen(["xdg-open", value])
                result = OSActionResult(action, True, f"Открыто: {value}")

            elif action == "powershell":
                output = self._powershell(str(args.get("command") or ""), timeout=int(args.get("timeout") or 60))
                result = OSActionResult(action, True, "PowerShell-команда выполнена", output)

            else:
                raise ValueError(f"Неизвестное действие ОС: {action}")
        except Exception as exc:
            result = OSActionResult(action, False, str(exc), None)
            problem("os_action_failed", str(exc), action=action, owner_id=str(owner_id)[:200])

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
            "run": ("powershell", {"command": tail}), "powershell": ("powershell", {"command": tail}),
        }
        return mapping.get(cmd)

    def _plan_natural(self, message: str) -> tuple[str, dict[str, Any]]:
        prompt = [
            ChatMessage("system", "/no_think\nТы планировщик локального Windows-инструмента Лешего. Верни ТОЛЬКО JSON без markdown. "
                        "Схема: {\"action\":\"...\",\"args\":{...}}. Разрешённые action: system_status,list_dir,read_text,write_text,make_dir,copy,move,delete,process_list,start_process,stop_process,open_path,powershell. "
                        "Выбирай powershell только когда пользователь явно просит выполнить команду/скрипт или задачу невозможно выразить другим action. Не придумывай пути, PID и имена программ, которых нет в запросе; если данных недостаточно, верни {\"action\":\"need_clarification\",\"args\":{\"question\":\"...\"}}."),
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

    def handle_user_request(self, message: str, *, owner_id: str = "web:local") -> str | None:
        if not self.is_request(message):
            return None
        direct = self._direct_command(message)
        if direct is not None:
            action, args = direct
        else:
            action, args = self._plan_natural(message)

        if action == "help":
            return (
                "Управление ОС включено. Можно просить естественным языком или использовать `/os`: "
                "`/os status`, `/os ps`, `/os ls ПУТЬ`, `/os cat ФАЙЛ`, `/os mkdir ПУТЬ`, "
                "`/os open ПУТЬ`, `/os run PowerShell-команда`."
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
        return {
            "enabled": True,
            "platform": platform.system(),
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
            sid = self._ensure_session(owner_id, session_id)
            now = utcnow()
            with self.storage.connection() as conn:
                conn.execute("INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)", (sid, "user", text, now))
                count = int(conn.execute("SELECT COUNT(*) AS c FROM messages WHERE session_id=?", (sid,)).fetchone()["c"])
                if count == 1:
                    conn.execute("UPDATE sessions SET title=?,title_source=?,updated_at=? WHERE id=?", (" ".join(text.split())[:80], "auto", now, sid))
            answer = controller.handle_user_request(text, owner_id=owner_id) or "Системное действие не распознано."
            return {"session_id": sid, "answer": answer, "created_at": self._save_assistant(sid, answer), "sources": [], "os_action": True}
        return original_chat(message, owner_id=owner_id, session_id=session_id)

    app.chat = MethodType(chat_with_os, app)
    return controller


__all__ = ["OperatingSystemController", "OSActionResult", "install_os_control"]
