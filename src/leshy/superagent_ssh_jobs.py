"""Owner-scoped, process-local background SSH plans with durable safe status.

An approved plan runs on an in-process daemon thread, never in an HTTP
request. SQLite stores progress/verdicts only: NO raw SSH commands, output,
passwords, approval tokens or model-provided content. The approval must be
consumed by the owner-only Web endpoint BEFORE a job is created.

A restart cannot replay or resume uncertain remote effects. The instance
identifier is deliberately ephemeral and a foreign running job requires
explicit human reconciliation before new SSH plans can run.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
import uuid
from typing import Any, Callable

from .superagent_ssh_plan import (
    execute_approved_ssh_plan, plan_approval_signature, validate_ssh_plan,
)
from .superagent_ssh_terminal import check_terminal_permission
from .superagent_verified_ssh import _node_ip


_INSTANCE = secrets.token_hex(16)
_BUSY = frozenset({"queued", "running"})
_END = frozenset({"completed", "stopped", "uncertain", "reviewed"})
_MAX_HISTORY = 25
_SCHEMA = """
CREATE TABLE IF NOT EXISTS superagent_ssh_plan_jobs (
    job_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    plan_sha256 TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    status TEXT NOT NULL,
    total INTEGER NOT NULL,
    confirmed INTEGER NOT NULL DEFAULT 0,
    current_step INTEGER,
    stop_requested INTEGER NOT NULL DEFAULT 0,
    safe_progress_json TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS superagent_ssh_one_active_plan
    ON superagent_ssh_plan_jobs ((1))
    WHERE status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS superagent_ssh_plan_owner_history
    ON superagent_ssh_plan_jobs (owner_id, created_at DESC);
"""
_DB_LOCK = threading.RLock()


def _db_init(storage: Any) -> None:
    with storage.connection() as conn:
        conn.executescript(_SCHEMA)


def _safe_row(row: Any) -> dict[str, Any]:
    d = dict(row)
    try:
        steps = json.loads(d["safe_progress_json"])
    except (ValueError, TypeError):
        steps = []
    status = str(d["status"])
    # Another app process (or a restarted app) may still have been executing
    # a command. Never claim its work stopped just because we cannot see it.
    if status in _BUSY and d["instance_id"] != _INSTANCE:
        status = "unresolved"
    return {
        "job_id": d["job_id"], "node_id": d["node_id"],
        "plan_sha256": d["plan_sha256"], "status": status,
        "total": d["total"], "confirmed_steps": d["confirmed"],
        "current_step": d["current_step"],
        "stop_requested": bool(d["stop_requested"]),
        "steps": steps[:6],
        "created_at": d["created_at"], "updated_at": d["updated_at"],
        "remaining_not_started": max(0, d["total"] - len(steps)),
        "auto_retry": False, "resumable": False,
    }


def _job_id(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError("Некорректный номер SSH-задания")
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise ValueError("Некорректный номер SSH-задания") from None


def list_ssh_plan_jobs(policy: Any, actor: Any, *, job_id: str = "") -> dict[str, Any]:
    policy._check_actor(actor)
    _db_init(policy.storage)
    with policy.storage.connection() as db:
        if job_id:
            rows = db.execute(
                "SELECT * FROM superagent_ssh_plan_jobs WHERE owner_id=? AND job_id=?",
                (actor.owner_id, _job_id(job_id)),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM superagent_ssh_plan_jobs WHERE owner_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (actor.owner_id, _MAX_HISTORY),
            ).fetchall()
    if job_id and not rows:
        raise PermissionError("SSH-задание не найдено")
    return {"jobs": [_safe_row(row) for row in rows]}


def cancel_ssh_plan_job(policy: Any, actor: Any, job_id: str) -> dict[str, Any]:
    """Cooperative cancel: blocks NEXT step; cannot interrupt active SSH I/O."""
    policy._check_actor(actor)
    _db_init(policy.storage)
    ident = _job_id(job_id)
    with _DB_LOCK, policy.storage.connection() as db:
        row = db.execute(
            "SELECT * FROM superagent_ssh_plan_jobs WHERE job_id=? AND owner_id=?",
            (ident, actor.owner_id),
        ).fetchone()
        if row is None:
            raise PermissionError("SSH-задание не найдено")
        if row["status"] in _BUSY:
            db.execute(
                "UPDATE superagent_ssh_plan_jobs SET stop_requested=1,updated_at=? "
                "WHERE job_id=? AND owner_id=?",
                (time.time(), ident, actor.owner_id),
            )
    return list_ssh_plan_jobs(policy, actor, job_id=ident)["jobs"][0]


def acknowledge_ssh_plan_job(policy: Any, actor: Any, job_id: str) -> dict[str, Any]:
    """Owner attests to out-of-band inspection after uncertain/restarted run.

    Never labels the operation successful, and cannot acknowledge an active
    thread from THIS process. Multi-process deployments require one SSH worker.
    """
    policy._check_actor(actor)
    _db_init(policy.storage)
    ident = _job_id(job_id)
    with _DB_LOCK, policy.storage.connection() as db:
        row = db.execute(
            "SELECT * FROM superagent_ssh_plan_jobs WHERE job_id=? AND owner_id=?",
            (ident, actor.owner_id),
        ).fetchone()
        if row is None:
            raise PermissionError("SSH-задание не найдено")
        if row["status"] not in {"uncertain", "queued", "running"}:
            raise ValueError("Это задание не требует сверки")
        if row["status"] in _BUSY and row["instance_id"] == _INSTANCE:
            raise PermissionError("SSH-задание ещё выполняется в этом процессе")
        db.execute(
            "UPDATE superagent_ssh_plan_jobs SET status='reviewed', "
            "stop_requested=1,updated_at=? WHERE job_id=? AND owner_id=?",
            (time.time(), ident, actor.owner_id),
        )
    return list_ssh_plan_jobs(policy, actor, job_id=ident)["jobs"][0]


def _job_can_continue(storage: Any, job_id: str, instance: str) -> bool:
    with storage.connection() as db:
        row = db.execute(
            "SELECT status,stop_requested FROM superagent_ssh_plan_jobs "
            "WHERE job_id=? AND instance_id=?",
            (job_id, instance),
        ).fetchone()
        return bool(row and row["status"] == "running" and not row["stop_requested"])


def _persist_progress(storage: Any, job_id: str, index: int,
                      phase: str, result: dict[str, Any] | None) -> None:
    with _DB_LOCK, storage.connection() as db:
        row = db.execute(
            "SELECT safe_progress_json FROM superagent_ssh_plan_jobs "
            "WHERE job_id=? AND instance_id=? AND status='running'",
            (job_id, _INSTANCE),
        ).fetchone()
        if row is None:
            raise PermissionError("SSH-задание больше не активно")
        progress = json.loads(row["safe_progress_json"])
        if phase == "started":
            # Record a possible remote effect BEFORE sending the action.
            progress.append({"index": index, "verdict": "in_progress"})
        elif phase == "finished" and result is not None:
            if not progress or progress[-1].get("index") != index:
                raise RuntimeError("Нарушен порядок SSH-шагов")
            progress[-1] = {
                "index": index, "verdict": str(result.get("verdict") or "unknown")[:40],
                "os_family": str(result.get("os_family") or "")[:20],
                "action_exit_code": result.get("action", {}).get("exit_code"),
                "check_exit_code": (result.get("verification") or {}).get("exit_code"),
            }
        else:
            raise ValueError("Недопустимое состояние SSH-задания")
        confirmed = sum(p["verdict"] == "confirmed" for p in progress)
        db.execute(
            "UPDATE superagent_ssh_plan_jobs SET safe_progress_json=?, "
            "confirmed=?,current_step=?,updated_at=? WHERE job_id=? AND instance_id=?",
            (json.dumps(progress, ensure_ascii=False, separators=(",", ":")),
             confirmed, index, time.time(), job_id, _INSTANCE),
        )


def _worker(policy: Any, actor: Any, job_id: str, node: str,
            credential_file: str, steps: list[dict[str, str]],
            may_continue: Callable[[], bool]) -> None:
    storage = policy.storage
    try:
        with _DB_LOCK, storage.connection() as db:
            row = db.execute(
                "SELECT status,stop_requested FROM superagent_ssh_plan_jobs "
                "WHERE job_id=? AND instance_id=?",
                (job_id, _INSTANCE),
            ).fetchone()
            if row is None:
                return
            if row["stop_requested"]:
                db.execute(
                    "UPDATE superagent_ssh_plan_jobs SET status='stopped',updated_at=? "
                    "WHERE job_id=? AND instance_id=?",
                    (time.time(), job_id, _INSTANCE),
                )
                return
            db.execute(
                "UPDATE superagent_ssh_plan_jobs SET status='running',updated_at=? "
                "WHERE job_id=? AND instance_id=? AND status='queued'",
                (time.time(), job_id, _INSTANCE),
            )
        def still_allowed() -> bool:
            return _job_can_continue(storage, job_id, _INSTANCE) and may_continue()

        result = execute_approved_ssh_plan(
            policy, actor, node, credential_file, steps,
            may_continue=still_allowed,
            on_step=lambda i, phase, value: _persist_progress(
                storage, job_id, i, phase, value,
            ),
        )
        status = str(result.get("status"))
        if status not in {"completed", "stopped", "uncertain"}:
            status = "uncertain"
    except Exception:
        # Could be a crash/error AFTER remote side-effects. Never auto-retry.
        status = "uncertain"
    with _DB_LOCK, storage.connection() as db:
        db.execute(
            "UPDATE superagent_ssh_plan_jobs SET status=?,updated_at=? "
            "WHERE job_id=? AND instance_id=? AND status='running'",
            (status, time.time(), job_id, _INSTANCE),
        )


def start_ssh_plan_job(policy: Any, actor: Any, node_id: str,
                       credential_file: str, steps: Any, *,
                       may_continue: Callable[[], bool]) -> dict[str, Any]:
    """Only invoke AFTER consuming exactly matching one-use Web approval."""
    if not callable(may_continue):
        raise ValueError("Требуется серверная проверка режима удалённого управления")
    normalized = validate_ssh_plan(steps)
    node = _node_ip(node_id)
    check_terminal_permission(policy, actor, node)
    _db_init(policy.storage)
    ident = str(uuid.uuid4())
    now = time.time()
    try:
        with _DB_LOCK, policy.storage.connection() as db:
            # A UNIQUE partial index prevents simultaneous active jobs even
            # when two HTTP requests race across workers/processes.
            db.execute(
                "INSERT INTO superagent_ssh_plan_jobs "
                "(job_id,owner_id,node_id,plan_sha256,instance_id,status,total,"
                "confirmed,current_step,stop_requested,safe_progress_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,'queued',?,0,NULL,0,'[]',?,?)",
                (ident, actor.owner_id, node,
                 plan_approval_signature(normalized).split(":")[-1],
                 _INSTANCE, len(normalized), now, now),
            )
    except sqlite3.IntegrityError as exc:
        raise PermissionError(
            "Другой SSH-план ещё не завершён или требует ручной сверки"
        ) from exc
    thread = threading.Thread(
        target=_worker, args=(
            policy, actor, ident, node, credential_file, normalized, may_continue,
        ), name="leshy-ssh-plan-" + ident[:8], daemon=True,
    )
    try:
        thread.start()
    except Exception:
        with _DB_LOCK, policy.storage.connection() as db:
            db.execute(
                "UPDATE superagent_ssh_plan_jobs SET status='uncertain',updated_at=? "
                "WHERE job_id=? AND instance_id=?",
                (time.time(), ident, _INSTANCE),
            )
        raise
    return list_ssh_plan_jobs(policy, actor, job_id=ident)["jobs"][0]


__all__ = [
    "list_ssh_plan_jobs", "start_ssh_plan_job",
    "cancel_ssh_plan_job", "acknowledge_ssh_plan_job",
]
