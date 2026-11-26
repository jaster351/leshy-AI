from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
import secrets
from typing import Any

from .os_control import OperatingSystemController, OSActionResult
from .storage import utcnow
from .os_control_guard import DISABLED
from .os_model_router import READ_ONLY_ACTIONS, MUTATING_ACTIONS, validate_os_plan
from .action_safety_guard import check_external_action


class ApprovalOperatingSystemController(OperatingSystemController):
    """Исполнитель ОС с независимым подтверждением изменяющих действий модели."""

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
        # Отдельная таблица: старый runtime при откате не может принять
        # запрос запуска команды или записи файла за старое удаление.
        with self.storage.connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS os_model_action_approvals ("
                "id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, session_id TEXT NOT NULL DEFAULT '', "
                "action TEXT NOT NULL, arguments_json TEXT NOT NULL, description TEXT NOT NULL, "
                "status TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, resolved_at TEXT NOT NULL DEFAULT '')"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_os_model_action_approvals_owner_status "
                "ON os_model_action_approvals(owner_id,status,created_at)"
            )
            # После смены версии или перезапуска нельзя оживлять старые
            # предложения модели, разрешения на которые могли быть отозваны.
            conn.execute(
                "UPDATE os_model_action_approvals SET status='cancelled',resolved_at=? "
                "WHERE status='pending'", (utcnow(),),
            )
        self._gate.on_disabled = self._revoke_pending
        # После рестарта при выключенной настройке старые кнопки опасных
        # действий не должны оживать при следующем включении ОС.
        if not self.enabled():
            self._revoke_pending()

    def _revoke_pending(self) -> dict[str, int]:
        with self.storage.connection() as conn:
            result = conn.execute(
                "UPDATE os_action_approvals SET status='cancelled',resolved_at=? "
                "WHERE status='pending'",
                (utcnow(),),
            )
            model_result = conn.execute(
                "UPDATE os_model_action_approvals SET status='cancelled',resolved_at=? "
                "WHERE status='pending'", (utcnow(),),
            )
        outcome = {"approvals_cancelled": result.rowcount + model_result.rowcount}
        if outcome["approvals_cancelled"]:
            self.storage.audit("os.control.pending_revoked", outcome)
        return outcome

    def sync_enabled(self):
        result = super().sync_enabled()
        if not self.enabled():
            # disable() уже вызывает callback при переходе 1→0.
            # Дополнительная сверка покрывает исходно выключенное состояние,
            # не обнуляя число ранее отозванных заявок в ответе UI.
            extra = self._revoke_pending()
            result["approvals_cancelled"] = (
                int(result.get("approvals_cancelled", 0))
                + int(extra.get("approvals_cancelled", 0))
            )
        return result

    def execute(self, action: str, args: dict[str, Any] | None = None, *, owner_id: str = "web:local", approved_delete: bool = False) -> OSActionResult:
        normalized = str(action or "").strip().lower()
        try:
            self._authorize_owner(owner_id)
        except PermissionError as exc:
            return OSActionResult(normalized, False, str(exc))
        if not self.enabled():
            return OSActionResult(normalized, False, DISABLED)
        values = dict(args or {})
        if normalized == "delete" and not approved_delete:
            return OSActionResult("delete", False, "Удаление возможно только после отдельного подтверждения владельца")
        if normalized in {"powershell", "native_shell"} and self._SHELL_DELETE_RE.search(str(values.get("command") or "")):
            return OSActionResult(
                normalized,
                False,
                "Удаляющие команды оболочки нельзя выполнять напрямую. Попросите Лешего удалить конкретный файл или папку — он покажет кнопки подтверждения.",
            )
        return super().execute(action, values, owner_id=owner_id)

    def request_delete(self, args: dict[str, Any], *, owner_id: str, session_id: str = "") -> dict[str, Any]:
        self._authorize_owner(owner_id)
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
        stored_args = dict(args)
        stored_args["path"] = str(path)
        description = f"Удалить {'папку' if path.is_dir() else 'файл'}: {path}"
        return self._store_approval("delete", stored_args, description, owner_id=owner_id,
                                    session_id=session_id, generation=generation)

    def request_model_approval(self, action, args, *, owner_id, session_id):
        """Сохранить ровно предложенный моделью план, но не исполнять его."""
        self._authorize_owner(owner_id)
        action, args = validate_os_plan(action, args)
        if action not in MUTATING_ACTIONS or action == "delete":
            raise ValueError("Недопустимый вид подтверждения")
        check_external_action(action, args)
        if action in {"powershell", "native_shell"} and self._SHELL_DELETE_RE.search(args["command"]):
            raise PermissionError("Удаление через оболочку запрещено. Требуется отдельное действие удаления файла.")
        # Не сокращаем план, который пользователь должен подтвердить.
        description = "Выполнить системное действие " + action + ": " + json.dumps(args, ensure_ascii=False)
        if len(description.encode("utf-16-le")) // 2 > 1800:
            raise ValueError("План слишком длинный для полного безопасного показа в Web и Telegram")
        generation = self._gate.begin()
        try:
            return self._store_approval(action, args, description, owner_id=owner_id,
                                        session_id=session_id, generation=generation)
        finally:
            self._gate.finish()

    def _store_approval(self, action, stored_args, description, *, owner_id, session_id, generation):
        self._gate.check(generation)
        table = "os_action_approvals" if action == "delete" else "os_model_action_approvals"
        approval_id = ("delete_" if action == "delete" else "model_") + secrets.token_urlsafe(18)
        created = datetime.now(timezone.utc)
        expires = created + timedelta(minutes=10)
        with self.storage.connection() as conn:
            conn.execute(
                f"INSERT INTO {table}(id,owner_id,session_id,action,arguments_json,description,status,created_at,expires_at,resolved_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    approval_id,
                    str(owner_id)[:200],
                    str(session_id)[:200],
                    action,
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
                    f"UPDATE {table} SET status='cancelled',resolved_at=? WHERE id=? AND status='pending'",
                    (utcnow(), approval_id),
                )
            raise
        # Не записываем команды и их аргументы в аудит; они остаются в закрытом запросе владельца.
        self.storage.audit("os.approval_requested", {"id": approval_id, "owner_id": str(owner_id)[:200], "action": action})
        return {
            "id": approval_id,
            "action": action,
            "description": description,
            "expires_at": expires.isoformat(),
            "yes_label": "Да, удалить" if action == "delete" else "Да, выполнить",
            "no_label": "Нет, отменить",
        }

    def pending(self, *, owner_id: str, session_id: str = "") -> list[dict[str, Any]]:
        self._authorize_owner(owner_id)
        if not self.enabled():
            return []
        owner = str(owner_id or "").strip()[:200]
        sid = str(session_id or "").strip()[:200]
        with self.storage.connection() as conn:
            # Обе таблицы принадлежат одному подтверждённому владельцу.
            rows = []
            for table in ("os_action_approvals", "os_model_action_approvals"):
                query = (
                    f"SELECT id,description,action,created_at,expires_at FROM {table} "
                    "WHERE owner_id=? AND status='pending'"
                )
                params = (owner,)
                if sid:
                    query += " AND session_id=?"
                    params = (owner, sid)
                rows.extend(conn.execute(query, params).fetchall())
        rows.sort(key=lambda row: str(row["created_at"]))
        now = datetime.now(timezone.utc)
        items: list[dict[str, Any]] = []
        for row in rows:
            expires = datetime.fromisoformat(str(row["expires_at"]))
            if now >= expires:
                with self.storage.connection() as conn:
                    table = self._approval_table(str(row["id"]))
                    conn.execute(f"UPDATE {table} SET status='expired',resolved_at=? WHERE id=? AND status='pending'", (utcnow(), str(row["id"])))
                continue
            items.append({
                "id": str(row["id"]),
                "action": str(row["action"]),
                "description": str(row["description"]),
                "created_at": str(row["created_at"]),
                "expires_at": str(row["expires_at"]),
                "yes_label": "Да, удалить" if str(row["action"]) == "delete" else "Да, выполнить",
                "no_label": "Нет, отменить",
            })
        return items

    @staticmethod
    def _approval_table(approval_id: str) -> str:
        # Имена таблиц — только константы, не пользовательский SQL.
        return "os_model_action_approvals" if approval_id.startswith("model_") else "os_action_approvals"

    def resolve(self, approval_id: str, *, owner_id: str, allow: bool) -> dict[str, Any]:
        """Одноразовое подтверждение: claim до исполнения, без повторных эффектов."""
        approval_id = str(approval_id or "").strip()
        table = self._approval_table(approval_id)
        owner = str(owner_id or "").strip()[:200]
        self._authorize_owner(owner)
        if not self.enabled():
            return {"id": approval_id, "status": "cancelled", "message": DISABLED}
        with self.storage.connection() as conn:
            row = conn.execute(
                f"SELECT * FROM {table} WHERE id=?", (approval_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Запрос подтверждения не найден")
        if str(row["owner_id"]) != owner:
            raise PermissionError("Подтвердить действие может только владелец запроса")
        action = str(row["action"])
        deletion = action == "delete"
        if action not in MUTATING_ACTIONS or deletion != (table == "os_action_approvals"):
            raise ValueError("Неизвестный тип подтверждаемого действия")
        status = str(row["status"])
        if status != "pending":
            return {"id": approval_id, "status": status,
                    "message": "Подтверждение уже обработано или требует проверки. Повтор запрещён."}
        if datetime.now(timezone.utc) >= datetime.fromisoformat(str(row["expires_at"])):
            with self.storage.connection() as conn:
                conn.execute(
                    f"UPDATE {table} SET status='expired',resolved_at=? "
                    "WHERE id=? AND status='pending'", (utcnow(), approval_id),
                )
            return {"id": approval_id, "status": "expired",
                    "message": "Время подтверждения истекло. Действие не выполнялось."}
        if not allow:
            with self.storage.connection() as conn:
                changed = conn.execute(
                    f"UPDATE {table} SET status='cancelled',resolved_at=? "
                    "WHERE id=? AND owner_id=? AND status='pending'",
                    (utcnow(), approval_id, owner),
                )
            if changed.rowcount != 1:
                return {"id": approval_id, "status": "already_handled",
                        "message": "Запрос уже выполняется. Повтор запрещён."}
            message = "Удаление отменено." if deletion else "Действие отменено."
            if row["session_id"]:
                self.app._save_assistant(str(row["session_id"]), message)
            self.storage.audit("os.approval_cancelled", {"id": approval_id, "owner_id": owner})
            return {"id": approval_id, "status": "cancelled", "message": message}

        # Только один поток/процесс может перейти pending -> executing.
        with self.storage.connection() as conn:
            claimed = conn.execute(
                f"UPDATE {table} SET status='executing' "
                "WHERE id=? AND owner_id=? AND status='pending'",
                (approval_id, owner),
            )
        if claimed.rowcount != 1:
            return {"id": approval_id, "status": "already_handled",
                    "message": "Действие уже выполняется; повтор запрещён."}
        result = None
        failure = ""
        try:
            self.assert_allowed()
            args = json.loads(str(row["arguments_json"]) or "{}")
            if deletion:
                result = OperatingSystemController.execute(self, "delete", args, owner_id=owner)
            else:
                action, args = validate_os_plan(action, args)
                result = self.execute(action, args, owner_id=owner)
        except Exception as exc:
            failure = type(exc).__name__
        if not self.enabled():
            final_status = "interrupted"
            message = (
                "Управление ОС отключено во время операции. Фактический "
                "результат операции не подтверждён: проверьте состояние вручную."
            )
        elif result is not None and result.ok:
            final_status = "executed"
            message = str(result.summary)
            if not deletion and result.data not in (None, "", [], {}):
                output = result.data if isinstance(result.data, str) else json.dumps(result.data, ensure_ascii=False, indent=2)
                message += "\n\n" + self._clip(output)
        else:
            final_status = "failed"
            message = ("Удаление не подтверждено; проверьте файл. " if deletion else
                       "Действие не подтверждено; проверьте состояние. ") + (
                str(result.summary) if result is not None else failure
            )
        with self.storage.connection() as conn:
            conn.execute(
                f"UPDATE {table} SET status=?,resolved_at=? "
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
        self._authorize_owner(owner_id)
        if not self.enabled():
            return DISABLED
        direct = self._direct_with_delete(message)
        if direct is not None:
            action, args = direct
        else:
            action, args = self._plan_natural(message)
        return self._handle_action(action, args, owner_id=owner_id, session_id=session_id)

    def handle_model_action(self, planned, *, owner_id, session_id):
        """Модель предлагает действие; полномочия и подтверждения остаются в коде."""
        self._authorize_owner(owner_id)
        if not self.enabled():
            return DISABLED
        action, args = validate_os_plan(*planned)
        if action in READ_ONLY_ACTIONS or action in {"need_clarification", "delete"}:
            return self._handle_action(action, args, owner_id=owner_id, session_id=session_id)
        if action not in MUTATING_ACTIONS:
            raise ValueError("Неизвестное действие модели")
        approval = self.request_model_approval(action, args, owner_id=owner_id, session_id=session_id)
        return {
            "approval_required": True,
            "approval": approval,
            "message": "Требуется Ваше подтверждение. " + approval["description"],
        }

    def _handle_action(self, action, args, *, owner_id, session_id):
        if action == "help":
            return (
                "Управление ОС включено. Можно просить естественным языком или использовать `/os`: "
                "`/os status`, `/os ps`, `/os ls ПУТЬ`, `/os cat ФАЙЛ`, `/os mkdir ПУТЬ`, "
                "`/os open ПУТЬ`, `/os delete ПУТЬ`, `/os run КОМАНДА для текущей ОС`. "
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
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT status,COUNT(*) AS n FROM (SELECT status FROM os_action_approvals "
                "UNION ALL SELECT status FROM os_model_action_approvals) "
                "WHERE status IN ('pending','executing','interrupted') "
                "GROUP BY status"
            ).fetchall()
        value["approval_counts"] = {str(row["status"]): int(row["n"]) for row in rows}
        value["unfinished_approval_warning"] = any(
            row["status"] in {"executing", "interrupted"} for row in rows
        )
        value["delete_requires_owner_approval"] = True
        value["model_mutation_requires_owner_approval"] = True
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
