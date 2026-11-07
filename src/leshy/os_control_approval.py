from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
import secrets
from typing import Any

from .os_control import OperatingSystemController, OSActionResult
from .storage import utcnow
from .os_control_guard import DISABLED


class ApprovalOperatingSystemController(OperatingSystemController):
    """OS controller, где любое удаление требует отдельного подтверждения владельца."""

    _SHELL_DELETE_RE = re.compile(
        r"(?i)(?:\bremove-item\b|\bdel(?:ete)?\b|\berase\b|\brm\b|\brmdir\b|\brd\b|\bremove-directory\b)"
    )

    def __init__(self, app):
        super().__init__(app)
        with self.storage.connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS os_action_approvals ("
                "id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, session_id TEXT NOT NULL DEFAULT '', "
                "action TEXT NOT NULL, arguments_json TEXT NOT NULL, description TEXT NOT NULL, "
                "status TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, resolved_at TEXT NOT NULL DEFAULT '')"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_os_action_approvals_owner_status "
                "ON os_action_approvals(owner_id,status,created_at)"
            )

    def sync_enabled(self):
        status = super().sync_enabled()
        if not self.enabled():
            with self.storage.connection() as conn:
                result = conn.execute(
                    "UPDATE os_action_approvals SET status='cancelled',resolved_at=? WHERE status='pending'",
                    (utcnow(),),
                )
                status["approvals_cancelled"] = result.rowcount
            self.storage.audit("os.control.disabled", status)
        return status

    def is_request(self, message: str) -> bool:
        text = str(message or "")
        return bool(super().is_request(text) or re.match(r"(?i)^\s*/os\s+(?:delete|rm)\b", text))

    def execute(self, action: str, args: dict[str, Any] | None = None, *, owner_id: str = "web:local", approved_delete: bool = False) -> OSActionResult:
        normalized = str(action or "").strip().lower()
        if not self.enabled():
            return OSActionResult(normalized, False, DISABLED)
        values = dict(args or {})
        if normalized == "delete" and not approved_delete:
            return OSActionResult("delete", False, "Удаление возможно только после отдельного подтверждения владельца")
        if normalized == "powershell" and self._SHELL_DELETE_RE.search(str(values.get("command") or "")):
            return OSActionResult(
                "powershell",
                False,
                "Удаляющие PowerShell-команды нельзя выполнять напрямую. Попросите Лешего удалить конкретный файл или папку — он покажет кнопки подтверждения.",
            )
        return super().execute(action, values, owner_id=owner_id)

    def request_delete(self, args: dict[str, Any], *, owner_id: str, session_id: str = "") -> dict[str, Any]:
        generation = self._gate.begin()
        try:
            return self._request_delete_guarded(args, owner_id=owner_id, session_id=session_id, generation=generation)
        finally:
            self._gate.finish()

    def _request_delete_guarded(self, args: dict[str, Any], *, owner_id: str, session_id: str, generation: int) -> dict[str, Any]:
        self._gate.check(generation)
        path = self._path(args.get("path"))
        if self._is_root_or_system(path):
            raise PermissionError("Корень диска и системные каталоги нельзя удалить через чат")
        approval_id = secrets.token_urlsafe(18)
        created = datetime.now(timezone.utc)
        expires = created + timedelta(minutes=10)
        stored_args = dict(args)
        stored_args["path"] = str(path)
        description = f"Удалить {'папку' if path.is_dir() else 'файл'}: {path}"
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO os_action_approvals(id,owner_id,session_id,action,arguments_json,description,status,created_at,expires_at,resolved_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    approval_id,
                    str(owner_id)[:200],
                    str(session_id)[:200],
                    "delete",
                    json.dumps(stored_args, ensure_ascii=False),
                    description,
                    "pending",
                    created.isoformat(),
                    expires.isoformat(),
                    "",
                ),
            )
        try:
            self._gate.check(generation)
        except PermissionError:
            with self.storage.connection() as conn:
                conn.execute(
                    "UPDATE os_action_approvals SET status='cancelled',resolved_at=? WHERE id=? AND status='pending'",
                    (utcnow(), approval_id),
                )
            raise
        self.storage.audit("os.approval_requested", {"id": approval_id, "owner_id": str(owner_id)[:200], "description": description})
        return {
            "id": approval_id,
            "action": "delete",
            "description": description,
            "expires_at": expires.isoformat(),
            "yes_label": "Да, удалить",
            "no_label": "Нет, отменить",
        }

    def pending(self, *, owner_id: str, session_id: str = "") -> list[dict[str, Any]]:
        if not self.enabled():
            return []
        owner = str(owner_id or "").strip()[:200]
        sid = str(session_id or "").strip()[:200]
        with self.storage.connection() as conn:
            if sid:
                rows = conn.execute(
                    "SELECT id,description,action,created_at,expires_at FROM os_action_approvals "
                    "WHERE owner_id=? AND session_id=? AND status='pending' ORDER BY created_at",
                    (owner, sid),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id,description,action,created_at,expires_at FROM os_action_approvals "
                    "WHERE owner_id=? AND status='pending' ORDER BY created_at",
                    (owner,),
                ).fetchall()
        now = datetime.now(timezone.utc)
        items: list[dict[str, Any]] = []
        for row in rows:
            expires = datetime.fromisoformat(str(row["expires_at"]))
            if now >= expires:
                with self.storage.connection() as conn:
                    conn.execute("UPDATE os_action_approvals SET status='expired',resolved_at=? WHERE id=? AND status='pending'", (utcnow(), str(row["id"])))
                continue
            items.append({
                "id": str(row["id"]),
                "action": str(row["action"]),
                "description": str(row["description"]),
                "created_at": str(row["created_at"]),
                "expires_at": str(row["expires_at"]),
                "yes_label": "Да, удалить",
                "no_label": "Нет, отменить",
            })
        return items

    def resolve(self, approval_id: str, *, owner_id: str, allow: bool) -> dict[str, Any]:
        """Одноразовое подтверждение: claim до исполнения, без повторных эффектов."""
        approval_id = str(approval_id or "").strip()
        owner = str(owner_id or "").strip()[:200]
        if not self.enabled():
            return {"id": approval_id, "status": "cancelled", "message": DISABLED}
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT * FROM os_action_approvals WHERE id=?", (approval_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Запрос подтверждения не найден")
        if str(row["owner_id"]) != owner:
            raise PermissionError("Подтвердить удаление может только владелец запроса")
        status = str(row["status"])
        if status != "pending":
            return {"id": approval_id, "status": status,
                    "message": "Подтверждение уже обработано или требует проверки. Повтор запрещён."}
        if datetime.now(timezone.utc) >= datetime.fromisoformat(str(row["expires_at"])):
            with self.storage.connection() as conn:
                conn.execute(
                    "UPDATE os_action_approvals SET status='expired',resolved_at=? "
                    "WHERE id=? AND status='pending'", (utcnow(), approval_id),
                )
            return {"id": approval_id, "status": "expired",
                    "message": "Время подтверждения истекло. Удаление не выполнялось."}
        if not allow:
            with self.storage.connection() as conn:
                changed = conn.execute(
                    "UPDATE os_action_approvals SET status='cancelled',resolved_at=? "
                    "WHERE id=? AND owner_id=? AND status='pending'",
                    (utcnow(), approval_id, owner),
                )
            if changed.rowcount != 1:
                return {"id": approval_id, "status": "already_handled",
                        "message": "Запрос уже выполняется. Повтор запрещён."}
            message = "Удаление отменено."
            if row["session_id"]:
                self.app._save_assistant(str(row["session_id"]), message)
            self.storage.audit("os.approval_cancelled", {"id": approval_id, "owner_id": owner})
            return {"id": approval_id, "status": "cancelled", "message": message}

        # Только один поток/процесс может перейти pending -> executing.
        with self.storage.connection() as conn:
            claimed = conn.execute(
                "UPDATE os_action_approvals SET status='executing' "
                "WHERE id=? AND owner_id=? AND status='pending'",
                (approval_id, owner),
            )
        if claimed.rowcount != 1:
            return {"id": approval_id, "status": "already_handled",
                    "message": "Удаление уже выполняется; повтор запрещён."}
        result = None
        failure = ""
        try:
            self.assert_allowed()
            args = json.loads(str(row["arguments_json"]) or "{}")
            result = OperatingSystemController.execute(self, "delete", args, owner_id=owner)
        except Exception as exc:
            failure = type(exc).__name__
        if not self.enabled():
            final_status = "interrupted"
            message = (
                "Управление ОС отключено во время операции. Фактический "
                "результат удаления не подтверждён: проверьте файл вручную."
            )
        elif result is not None and result.ok:
            final_status = "executed"
            message = str(result.summary)
        else:
            final_status = "failed"
            message = "Удаление не подтверждено; проверьте файл. " + (
                str(result.summary) if result is not None else failure
            )
        with self.storage.connection() as conn:
            conn.execute(
                "UPDATE os_action_approvals SET status=?,resolved_at=? "
                "WHERE id=? AND owner_id=? AND status='executing'",
                (final_status, utcnow(), approval_id, owner),
            )
        if row["session_id"]:
            self.app._save_assistant(str(row["session_id"]), message)
        self.storage.audit("os.approval_resolved", {
            "id": approval_id, "owner_id": owner, "allowed": True, "status": final_status,
        })
        return {"id": approval_id, "status": final_status,
                "message": message, "ok": final_status == "executed"}

    def _direct_with_delete(self, message: str) -> tuple[str, dict[str, Any]] | None:
        text = str(message or "").strip()
        match = re.match(r"(?is)^/os\s+(?:delete|rm)\s+(.+)$", text)
        if match:
            return "delete", {"path": match.group(1).strip(), "recursive": True}
        return self._direct_command(message)

    def handle_user_request(self, message: str, *, owner_id: str = "web:local", session_id: str = "") -> str | dict[str, Any] | None:
        if not self.is_request(message):
            return None
        if not self.enabled():
            return DISABLED
        direct = self._direct_with_delete(message)
        if direct is not None:
            action, args = direct
        else:
            action, args = self._plan_natural(message)
        if action == "help":
            return (
                "Управление ОС включено. Можно просить естественным языком или использовать `/os`: "
                "`/os status`, `/os ps`, `/os ls ПУТЬ`, `/os cat ФАЙЛ`, `/os mkdir ПУТЬ`, "
                "`/os open ПУТЬ`, `/os delete ПУТЬ`, `/os run PowerShell-команда`. "
                "Любое удаление выполняется только после Вашего подтверждения."
            )
        if action == "need_clarification":
            return str(args.get("question") or "Уточните системное действие.")
        if action == "delete":
            approval = self.request_delete(args, owner_id=owner_id, session_id=session_id)
            return {
                "approval_required": True,
                "approval": approval,
                "message": "Требуется Ваше подтверждение. " + approval["description"],
            }
        result = self.execute(action, args, owner_id=owner_id)
        if not result.ok:
            return "Не удалось выполнить действие в ОС: " + result.summary
        if result.data in (None, "", [], {}):
            return result.summary
        rendered = result.data if isinstance(result.data, str) else json.dumps(result.data, ensure_ascii=False, indent=2)
        return result.summary + "\n\n" + self._clip(rendered)

    def status(self) -> dict[str, Any]:
        value = super().status()
        value["delete_requires_owner_approval"] = True
        value["shell_delete_bypass_blocked"] = True
        value["approval_ttl_minutes"] = 10
        return value


def install_os_control(app) -> ApprovalOperatingSystemController:
    existing = getattr(app, "os_control", None)
    if isinstance(existing, ApprovalOperatingSystemController):
        return existing
    controller = ApprovalOperatingSystemController(app)
    app.os_control = controller
    return controller


__all__ = ["ApprovalOperatingSystemController", "install_os_control"]
