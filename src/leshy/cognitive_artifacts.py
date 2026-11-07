"""Подтверждаемое владельцем создание текстовых артефактов SuperAgent.

Отдельный allow-list: только новый UTF-8 Markdown в каталоге рабочего
пространства. Никогда не выполняются shell/OS-команды, код или перезапись.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import time
import uuid
from typing import Any

from .storage import Storage, utcnow


_SCHEMA = """
CREATE TABLE IF NOT EXISTS superagent_artifacts(
    id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL UNIQUE REFERENCES superagent_tickets(id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','applying','applied','rejected','expired','failed')),
    expires_epoch REAL NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_superagent_artifacts_owner ON superagent_artifacts(owner_id,state);
"""

_ARTIFACT_INTENT = re.compile(
    r"(?iu)(созда[йть]|напиши|подготовь|составь|сохрани|сгенерируй|"
    r"сделай|оформи).{0,140}(файл|документ|заметк|отч[её]т|стать|"
    r"план|текст|инструкц|резюме)"
)


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class CognitiveArtifactGate:
    """Двухфазный Human Approval Gate с проверкой эффекта на диске."""

    def __init__(self, storage: Storage, workspace_root: Path):
        self.storage = storage
        self.workspace_root = Path(workspace_root)
        with storage.connection() as conn:
            conn.executescript(_SCHEMA)

    def propose(self, ticket_id: str, owner_id: str, title: str, content: str) -> dict[str, Any]:
        owner = str(owner_id or "").strip()
        text = str(content or "")
        heading = " ".join(str(title or "").split())[:120] or "Документ"
        if not owner or len(owner) > 200:
            raise ValueError("Не определён владелец")
        if not text.strip() or len(text.encode("utf-8")) > 16000:
            raise ValueError("Документ должен содержать от 1 до 16000 байт UTF-8")
        with self.storage.connection() as conn:
            ticket = conn.execute(
                "SELECT owner_id,request_text,state FROM superagent_tickets WHERE id=?",
                (str(ticket_id),),
            ).fetchone()
            if ticket is None or ticket["owner_id"] != owner:
                raise PermissionError("Задание принадлежит другому пользователю")
            if ticket["state"] != "working":
                raise ValueError("Предложение разрешено только во время исполнения задания")
            if not _ARTIFACT_INTENT.search(str(ticket["request_text"])):
                raise PermissionError("Исходное задание не разрешало создание документа")
            existing = conn.execute(
                "SELECT id,sha256,state FROM superagent_artifacts WHERE ticket_id=?",
                (str(ticket_id),),
            ).fetchone()
            digest = _sha(text.encode("utf-8"))
            if existing:
                if existing["sha256"] != digest:
                    raise ValueError("Для задания уже создано другое предложение")
                return self.get(str(existing["id"]), owner)
            identity = "ART-" + uuid.uuid4().hex
            conn.execute(
                "INSERT INTO superagent_artifacts"
                "(id,ticket_id,owner_id,title,content,sha256,state,expires_epoch,created_at) "
                "VALUES(?,?,?,?,?,?,'pending',?,?)",
                (identity, str(ticket_id), owner, heading, text, digest,
                 time.time() + 86400, utcnow()),
            )
        return self.get(identity, owner)

    def _row(self, artifact_id: str, owner_id: str) -> dict[str, Any]:
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT * FROM superagent_artifacts WHERE id=? AND owner_id=?",
                (str(artifact_id), str(owner_id or "").strip()),
            ).fetchone()
        if row is None:
            raise PermissionError("Артефакт не найден или принадлежит другому владельцу")
        return dict(row)

    def get(self, artifact_id: str, owner_id: str) -> dict[str, Any]:
        row = self._row(artifact_id, owner_id)
        return {
            "id": row["id"], "ticket_id": row["ticket_id"],
            "title": row["title"], "state": row["state"],
            "sha256": row["sha256"], "content": row["content"],
            "expires_epoch": row["expires_epoch"],
        }

    def _target(self, row: dict[str, Any]) -> Path:
        # Имя файла определяет сервер, а не модель; нет traversal и overwrite.
        base = self.workspace_root.expanduser().resolve()
        owner_key = _sha(row["owner_id"].encode("utf-8"))[:20]
        directory = base / "cognitive-artifacts" / owner_key
        directory.mkdir(parents=True, exist_ok=True)
        real = directory.resolve()
        if not real.is_relative_to(base):
            raise PermissionError("Путь артефакта вне рабочего пространства")
        target = real / (row["id"] + ".md")
        if target.is_symlink():
            raise PermissionError("Символическая ссылка вместо файла запрещена")
        return target

    def approve(self, artifact_id: str, owner_id: str) -> dict[str, Any]:
        row = self._row(artifact_id, owner_id)
        if row["state"] == "applied":
            # Идемпотентный ответ возможен только при сохранённом эффекте.
            target = self._target(row)
            if not target.is_file() or _sha(target.read_bytes()) != row["sha256"]:
                raise ValueError("Подтверждение есть, но файл утрачен или изменён; требуется проверка")
            return {"id": row["id"], "state": "applied",
                    "path": str(target), "sha256": row["sha256"]}
        if row["state"] != "pending":
            raise ValueError("Артефакт уже обработан; повторное выполнение запрещено")
        if time.time() >= float(row["expires_epoch"]):
            with self.storage.connection() as conn:
                conn.execute("UPDATE superagent_artifacts SET state='expired',finished_at=? "
                             "WHERE id=? AND state='pending'", (utcnow(), row["id"]))
            raise ValueError("Время подтверждения истекло")
        with self.storage.connection() as conn:
            ticket = conn.execute("SELECT state,owner_id FROM superagent_tickets WHERE id=?",
                                  (row["ticket_id"],)).fetchone()
            if ticket is None or ticket["owner_id"] != row["owner_id"] or ticket["state"] != "waiting":
                raise ValueError("Задание больше не ожидает подтверждения")
            changed = conn.execute(
                "UPDATE superagent_artifacts SET state='applying' WHERE id=? AND state='pending'",
                (row["id"],),
            )
            if changed.rowcount != 1:
                raise ValueError("Подтверждение уже выполняется")
        # Если процесс падает после создания файла, дальнейший approve не
        # повторит запись: state='applying'. Требуется ручная сверка.
        try:
            target = self._target(row)
            payload = row["content"].encode("utf-8")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(target, flags, 0o600)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                raise
            if _sha(target.read_bytes()) != row["sha256"]:
                raise OSError("Проверка SHA-256 созданного файла не пройдена")
            with self.storage.connection() as conn:
                conn.execute(
                    "UPDATE superagent_artifacts SET state='applied',finished_at=? "
                    "WHERE id=? AND state='applying'",
                    (utcnow(), row["id"]),
                )
            return {"id": row["id"], "state": "applied",
                    "path": str(target), "sha256": row["sha256"]}
        except Exception:
            # Не удаляем и не перезаписываем возможный частичный файл.
            with self.storage.connection() as conn:
                conn.execute(
                    "UPDATE superagent_artifacts SET state='failed',finished_at=? "
                    "WHERE id=? AND state='applying'",
                    (utcnow(), row["id"]),
                )
            raise

    def reject(self, artifact_id: str, owner_id: str) -> dict[str, Any]:
        row = self._row(artifact_id, owner_id)
        if row["state"] == "rejected":
            return {"id": row["id"], "state": "rejected"}
        if row["state"] != "pending":
            raise ValueError("Отклонение недоступно: предложение уже обработано")
        with self.storage.connection() as conn:
            changed = conn.execute(
                "UPDATE superagent_artifacts SET state='rejected',finished_at=? "
                "WHERE id=? AND state='pending'",
                (utcnow(), row["id"]),
            )
        if changed.rowcount != 1:
            raise ValueError("Предложение уже обработано")
        return {"id": row["id"], "state": "rejected"}


__all__ = ["CognitiveArtifactGate"]
