"""Проверяемый и обратимый откат только созданных SuperAgent документов.

Откат по команде владельца переносит исходный файл в архив через
жёсткую ссылку с O_EXCL-семантикой и удаление только старого имени.
Содержимое сохраняется. После сбоя никаких файловых изменений при
восстановлении не производится.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import time
from typing import Any

from .cognitive_artifacts import CognitiveArtifactGate, _sha
from .storage import utcnow


_SCHEMA = """
CREATE TABLE IF NOT EXISTS superagent_artifact_rollbacks(
    artifact_id TEXT PRIMARY KEY REFERENCES superagent_artifacts(id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK(state IN ('applying','archived','failed')),
    created_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT ''
);
"""


class CognitiveArtifactRollback:
    def __init__(self, gate: CognitiveArtifactGate):
        self.gate = gate
        self.storage = gate.storage
        with self.storage.connection() as conn:
            conn.executescript(_SCHEMA)

    def _row(self, artifact_id: str) -> dict[str, Any] | None:
        with self.storage.connection() as conn:
            result = conn.execute(
                "SELECT * FROM superagent_artifact_rollbacks WHERE artifact_id=?",
                (str(artifact_id),),
            ).fetchone()
        return dict(result) if result else None

    def _archive_target(self, row: dict[str, Any], *, create_parent: bool = False) -> Path:
        base = self.gate.workspace_root.expanduser().resolve()
        key = hashlib.sha256(row["owner_id"].encode("utf-8")).hexdigest()[:20]
        directory = base / "cognitive-artifacts" / ".rollback" / key
        if create_parent:
            directory.mkdir(parents=True, exist_ok=True)
        real = directory.resolve()
        if not real.is_relative_to(base):
            raise PermissionError("Архив вне рабочего пространства")
        destination = real / (row["id"] + ".md")
        if destination.is_symlink():
            raise PermissionError("Символическая ссылка в архиве запрещена")
        return destination

    def _verified_archive(self, row: dict[str, Any]) -> Path | None:
        try:
            target = self._archive_target(row)
            if target.is_symlink() or not target.is_file() or target.stat().st_size > 16000:
                return None
            with target.open("rb") as stream:
                data = stream.read(16001)
            return target if len(data) <= 16000 and _sha(data) == row["sha256"] else None
        except (OSError, ValueError, PermissionError):
            return None

    def _source_absent(self, row: dict[str, Any]) -> bool:
        """Не считать архив успешным, если по старому пути есть ЛЮБОЙ файл.

        Даже повреждённый или заменённый файл в активном каталоге означает,
        что откат не завершён. Сверка сама не удаляет и не перезаписывает его.
        """
        try:
            source = self.gate._target(row, create_parent=False)
            return not source.exists() and not source.is_symlink()
        except (OSError, ValueError, PermissionError):
            return False

    def verify(self, artifact_id: str, owner_id: str) -> dict[str, Any]:
        """Только чтение: нельзя объявить успех без совпадения SHA-256."""
        row = self.gate._row(artifact_id, owner_id)
        rollback = self._row(row["id"])
        if rollback:
            archive = self._verified_archive(row)
            verified = bool(
                rollback["state"] == "archived" and archive is not None
                and self._source_absent(row)
            )
            return {
                "id": row["id"], "state": "rolled_back" if verified else rollback["state"],
                "verified": verified, "sha256": row["sha256"],
                "path": str(archive) if verified else "",
            }
        if row["state"] != "applied":
            return {"id": row["id"], "state": row["state"], "verified": False,
                    "sha256": row["sha256"], "path": ""}
        active = self.gate._verified_existing(row)
        return {
            "id": row["id"], "state": "applied" if active else "mismatch",
            "verified": active is not None, "sha256": row["sha256"],
            "path": str(active) if active else "",
        }

    def rollback(self, artifact_id: str, owner_id: str) -> dict[str, Any]:
        """Явная команда владельца; никаких изменений без прошедшей проверки."""
        row = self.gate._row(artifact_id, owner_id)
        record = self._row(row["id"])
        if record:
            result = self.verify(artifact_id, owner_id)
            if record["state"] == "archived" and result["verified"]:
                return result
            raise ValueError("Откат уже начат или требует ручной сверки")
        if row["state"] != "applied":
            raise ValueError("Откат возможен только для подтверждённого документа")
        active = self.gate._verified_existing(row)
        if active is None:
            raise ValueError("Файл отсутствует или изменён; откат запрещён")
        with self.storage.connection() as conn:
            ticket = conn.execute(
                "SELECT state,owner_id FROM superagent_tickets WHERE id=?",
                (row["ticket_id"],),
            ).fetchone()
            if ticket is None or ticket["owner_id"] != row["owner_id"] or ticket["state"] != "ready":
                raise ValueError("Исходное задание не находится в завершённом состоянии")
            conn.execute(
                "INSERT INTO superagent_artifact_rollbacks"
                "(artifact_id,state,created_at) VALUES(?,'applying',?)",
                (row["id"], utcnow()),
            )
        try:
            archive = self._archive_target(row, create_parent=True)
            # os.link создаёт имя только если его ещё нет, без перезаписи.
            # Архив находится на том же диске, что и исходный документ.
            os.link(active, archive, follow_symlinks=False)
            if self._verified_archive(row) is None:
                raise OSError("Архивный файл не прошёл проверку SHA-256")
            # Проверяем inode: не удаляем подменённый между проверками файл.
            original_stat = active.stat(follow_symlinks=False)
            archive_stat = archive.stat(follow_symlinks=False)
            if (
                original_stat.st_ino != archive_stat.st_ino
                or original_stat.st_dev != archive_stat.st_dev
                or self.gate._verified_existing(row) is None
            ):
                raise OSError("Исходный файл изменился во время отката")
            active.unlink()
            # В этот момент запись ещё applying, поэтому проверяем оба пути.
            if self._verified_archive(row) is None or not self._source_absent(row):
                raise OSError("Не удалось подтвердить перемещение в архив")
            with self.storage.connection() as conn:
                now = utcnow()
                conn.execute(
                    "UPDATE superagent_artifact_rollbacks SET state='archived',finished_at=? "
                    "WHERE artifact_id=? AND state='applying'",
                    (now, row["id"]),
                )
                conn.execute(
                    "UPDATE superagent_tickets SET state='blocked',attention='',"
                    "latest_report=?,delivered=0,updated_at=? "
                    "WHERE id=? AND owner_id=? AND state='ready'",
                    ("Документ перемещён в архив без удаления данных: " + str(archive),
                     now, row["ticket_id"], row["owner_id"]),
                )
            return self.verify(artifact_id, owner_id)
        except Exception:
            # Даже если оба имени существуют, не удаляем архив автоматически.
            with self.storage.connection() as conn:
                conn.execute(
                    "UPDATE superagent_artifact_rollbacks SET state='failed',finished_at=? "
                    "WHERE artifact_id=? AND state='applying'",
                    (utcnow(), row["id"]),
                )
            raise

    def reconcile(self) -> dict[str, Any]:
        """Никаких файловых изменений: обнаружить, дошёл ли откат до диска."""
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT r.artifact_id,r.state AS rollback_state,a.*,"
                "t.state AS ticket_state "
                "FROM superagent_artifact_rollbacks r "
                "JOIN superagent_artifacts a ON a.id=r.artifact_id "
                "JOIN superagent_tickets t ON t.id=a.ticket_id "
                "WHERE r.state IN ('applying','failed') "
                "OR (r.state='archived' AND t.state='ready')"
            ).fetchall()
        counts = {"archived": 0, "failed": 0, "ticket_ids": []}
        for fetched in rows:
            row = dict(fetched)
            state = str(row["rollback_state"])
            archive = self._verified_archive(row)
            target_state = "archived" if archive is not None and self._source_absent(row) else "failed"
            with self.storage.connection() as conn:
                now = utcnow()
                if state != target_state:
                    result = conn.execute(
                        "UPDATE superagent_artifact_rollbacks SET state=?,finished_at=? "
                        "WHERE artifact_id=? AND state=?",
                        (target_state, now, row["id"], state),
                    )
                    if result.rowcount != 1:
                        continue
                if target_state == "archived":
                    changed = conn.execute(
                        "UPDATE superagent_tickets SET state='blocked',attention='',"
                        "latest_report=?,delivered=0,updated_at=? "
                        "WHERE id=? AND owner_id=? AND state='ready'",
                        ("Откат подтверждён: документ сохранён в архиве.",
                         now, row["ticket_id"], row["owner_id"]),
                    )
                else:
                    changed = conn.execute(
                        "UPDATE superagent_tickets SET state='blocked',attention='',"
                        "latest_report=?,delivered=0,updated_at=? "
                        "WHERE id=? AND owner_id=? AND state='ready'",
                        ("Откат не подтверждён, нужна ручная проверка файлов.",
                         now, row["ticket_id"], row["owner_id"]),
                    )
            if state != target_state or changed.rowcount:
                counts[target_state] += 1
            if changed.rowcount:
                counts["ticket_ids"].append(str(row["ticket_id"]))
        return counts


__all__ = ["CognitiveArtifactRollback"]
