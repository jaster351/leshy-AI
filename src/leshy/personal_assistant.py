from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from .safety import SafetyPolicy
from .storage import Storage, utcnow


SECRET_MARKERS = (
    "password",
    "passwd",
    "token",
    "secret",
    "cookie",
    "authorization",
    "пароль",
    "токен",
    "куки",
    "секрет",
)


@dataclass(frozen=True)
class AssistantTask:
    id: int
    owner_id: str
    request: str
    status: str


@dataclass(frozen=True)
class AssistantAction:
    id: int
    task_id: int
    kind: str
    description: str
    payload: dict[str, Any]
    risk: str
    requires_approval: bool
    status: str


class PersonalAssistant:
    """Постоянный контур бытовых, жизненных и рабочих поручений.

    Класс хранит цель и журнал шагов, но не хранит пароли, токены или cookies.
    Для доступа к уже авторизованным сервисам в payload разрешается передавать
    только непрозрачную локальную ссылку вроде `session_ref`.

    Действия без внешних последствий могут выполняться автоматически. Оплата,
    отправка формы, оформление заказа, удаление и другие значимые внешние действия
    проходят через SafetyPolicy и останавливаются до явного подтверждения человека.
    """

    def __init__(self, storage: Storage, safety: SafetyPolicy | None = None):
        self.storage = storage
        self.safety = safety or SafetyPolicy()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.storage.connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS assistant_tasks(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL,
                    request TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','paused','done','failed','cancelled')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_assistant_tasks_owner_status ON assistant_tasks(owner_id,status,id DESC)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS assistant_actions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL REFERENCES assistant_tasks(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    description TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    risk TEXT NOT NULL,
                    requires_approval INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL CHECK(status IN ('ready','waiting_approval','approved','rejected','done','failed')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    approved_at TEXT,
                    approved_by TEXT,
                    executed_at TEXT,
                    result_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_assistant_actions_task_status ON assistant_actions(task_id,status,id)"
            )

    def create_task(self, owner_id: str, request: str) -> int:
        owner = " ".join(str(owner_id).split()).strip()
        text = " ".join(str(request).split()).strip()
        if not owner:
            raise ValueError("Не указан владелец поручения")
        if not text:
            raise ValueError("Поручение не может быть пустым")
        decision = self.safety.evaluate_text(text)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        now = utcnow()
        with self.storage.connection() as conn:
            cur = conn.execute(
                "INSERT INTO assistant_tasks(owner_id,request,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                (owner, text, "active", now, now),
            )
            task_id = int(cur.lastrowid)
        self.storage.audit("assistant.task_created", {"id": task_id, "owner_id": owner, "request": text})
        return task_id

    def propose_action(
        self,
        task_id: int,
        *,
        kind: str,
        description: str,
        payload: dict[str, Any] | None = None,
        destructive: bool = False,
        external_effect: bool = False,
    ) -> int:
        payload = dict(payload or {})
        self._validate_payload(payload)
        description = " ".join(str(description).split()).strip()
        kind = " ".join(str(kind).split()).strip()
        if not kind or not description:
            raise ValueError("Шаг поручения должен содержать тип и описание")
        self._require_active_task(task_id)

        decision = self.safety.evaluate_action(
            description,
            destructive=bool(destructive),
            external_effect=bool(external_effect),
        )
        status = "rejected" if not decision.allowed else (
            "waiting_approval" if decision.requires_human_approval else "ready"
        )
        now = utcnow()
        with self.storage.connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO assistant_actions(
                    task_id,kind,description,payload_json,risk,requires_approval,status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(task_id),
                    kind,
                    description,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    decision.risk,
                    int(decision.requires_human_approval),
                    status,
                    now,
                    now,
                ),
            )
            action_id = int(cur.lastrowid)
        self.storage.audit(
            "assistant.action_proposed",
            {
                "id": action_id,
                "task_id": int(task_id),
                "kind": kind,
                "description": description,
                "risk": decision.risk,
                "requires_approval": bool(decision.requires_human_approval),
                "status": status,
            },
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)
        return action_id

    def approve_action(self, action_id: int, *, approved_by: str = "user") -> None:
        row = self._action_row(action_id)
        if str(row["status"]) != "waiting_approval":
            raise ValueError("Этот шаг не ожидает подтверждения")
        now = utcnow()
        with self.storage.connection() as conn:
            conn.execute(
                "UPDATE assistant_actions SET status='approved',approved_at=?,approved_by=?,updated_at=? WHERE id=?",
                (now, str(approved_by), now, int(action_id)),
            )
        self.storage.audit("assistant.action_approved", {"id": int(action_id), "approved_by": str(approved_by)})

    def reject_action(self, action_id: int, *, rejected_by: str = "user") -> None:
        row = self._action_row(action_id)
        if str(row["status"]) not in {"waiting_approval", "approved", "ready"}:
            raise ValueError("Этот шаг уже нельзя отклонить")
        with self.storage.connection() as conn:
            conn.execute(
                "UPDATE assistant_actions SET status='rejected',updated_at=? WHERE id=?",
                (utcnow(), int(action_id)),
            )
        self.storage.audit("assistant.action_rejected", {"id": int(action_id), "rejected_by": str(rejected_by)})

    def can_execute(self, action_id: int) -> bool:
        row = self._action_row(action_id)
        return str(row["status"]) in {"ready", "approved"}

    def mark_executed(self, action_id: int, result: dict[str, Any] | None = None) -> None:
        if not self.can_execute(action_id):
            raise PermissionError("Шаг нельзя выполнить без требуемого подтверждения")
        now = utcnow()
        with self.storage.connection() as conn:
            conn.execute(
                "UPDATE assistant_actions SET status='done',executed_at=?,updated_at=?,result_json=? WHERE id=?",
                (now, now, json.dumps(result or {}, ensure_ascii=False, sort_keys=True), int(action_id)),
            )
        self.storage.audit("assistant.action_done", {"id": int(action_id), "result": result or {}})

    def finish_task(self, task_id: int) -> None:
        self._require_active_task(task_id)
        with self.storage.connection() as conn:
            pending = conn.execute(
                "SELECT count(*) n FROM assistant_actions WHERE task_id=? AND status IN ('ready','waiting_approval','approved')",
                (int(task_id),),
            ).fetchone()
            if pending and int(pending["n"]) > 0:
                raise RuntimeError("Нельзя завершить поручение, пока есть незавершённые шаги")
            conn.execute(
                "UPDATE assistant_tasks SET status='done',updated_at=? WHERE id=?",
                (utcnow(), int(task_id)),
            )
        self.storage.audit("assistant.task_done", {"id": int(task_id)})

    def get_task(self, task_id: int) -> dict[str, Any]:
        with self.storage.connection() as conn:
            task = conn.execute("SELECT * FROM assistant_tasks WHERE id=?", (int(task_id),)).fetchone()
            if not task:
                raise KeyError(task_id)
            actions = conn.execute(
                "SELECT * FROM assistant_actions WHERE task_id=? ORDER BY id",
                (int(task_id),),
            ).fetchall()
        return {
            "task": dict(task),
            "actions": [self._decode_action(row) for row in actions],
        }

    def _require_active_task(self, task_id: int) -> None:
        with self.storage.connection() as conn:
            row = conn.execute("SELECT status FROM assistant_tasks WHERE id=?", (int(task_id),)).fetchone()
        if not row:
            raise KeyError(task_id)
        if str(row["status"]) != "active":
            raise RuntimeError("Поручение сейчас не активно")

    def _action_row(self, action_id: int):
        with self.storage.connection() as conn:
            row = conn.execute("SELECT * FROM assistant_actions WHERE id=?", (int(action_id),)).fetchone()
        if not row:
            raise KeyError(action_id)
        return row

    @staticmethod
    def _decode_action(row) -> dict[str, Any]:
        item = dict(row)
        try:
            item["payload"] = json.loads(str(item.pop("payload_json")))
        except Exception:
            item["payload"] = {}
        try:
            item["result"] = json.loads(str(item.pop("result_json")))
        except Exception:
            item["result"] = {}
        item["requires_approval"] = bool(item["requires_approval"])
        return item

    @classmethod
    def _validate_payload(cls, payload: Any, path: str = "payload") -> None:
        if isinstance(payload, dict):
            for key, value in payload.items():
                normalized = str(key).lower().replace("-", "_")
                if any(marker in normalized for marker in SECRET_MARKERS):
                    raise ValueError(
                        f"Нельзя сохранять секрет в поручении ({path}.{key}); используйте локальную session_ref"
                    )
                cls._validate_payload(value, f"{path}.{key}")
        elif isinstance(payload, (list, tuple)):
            for index, value in enumerate(payload):
                cls._validate_payload(value, f"{path}[{index}]")
