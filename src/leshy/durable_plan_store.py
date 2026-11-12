"""Durable dependency graphs for autonomous tasks, without executing tools.

SQLite leases protect a step from being completed by an obsolete worker.
Plan content stays scoped to the owner; no LLM or network work occurs here.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
from typing import Any


_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MAX_STEPS = 64


class PlanError(ValueError):
    pass


def _validate_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(steps, list) or not 1 <= len(steps) <= _MAX_STEPS:
        raise PlanError("Plan must contain 1–64 steps")
    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    for step in steps:
        if not isinstance(step, dict):
            raise PlanError("Step must be an object")
        ident, title = step.get("id"), step.get("title")
        deps = step.get("depends_on", [])
        if not isinstance(ident, str) or not _NAME.fullmatch(ident) or ident in ids:
            raise PlanError("Invalid or duplicate step ID")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 300:
            raise PlanError("Invalid step title")
        if not isinstance(deps, list) or len(deps) > _MAX_STEPS or not all(isinstance(d, str) for d in deps):
            raise PlanError("Invalid dependencies")
        if len(deps) != len(set(deps)):
            raise PlanError("Duplicate dependency")
        attempts = step.get("max_attempts", 3)
        if type(attempts) is not int or not 1 <= attempts <= 8:
            raise PlanError("max_attempts must be 1–8")
        ids.add(ident)
        normalized.append({"id": ident, "title": title.strip(), "depends_on": deps, "max_attempts": attempts})
    remaining = {s["id"]: set(s["depends_on"]) for s in normalized}
    if any(name not in remaining for deps in remaining.values() for name in deps):
        raise PlanError("Unknown dependency")
    while remaining:
        ready = {name for name, deps in remaining.items() if not deps}
        if not ready:
            raise PlanError("Circular dependencies")
        for name in ready:
            del remaining[name]
        for deps in remaining.values():
            deps.difference_update(ready)
    return normalized


class DurablePlanStore:
    """Crash-safe graph store. Each operation uses a separate DB connection."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS durable_plans (
                    plan_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    state TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS durable_plan_steps (
                    plan_id TEXT NOT NULL REFERENCES durable_plans(plan_id) ON DELETE CASCADE,
                    step_id TEXT NOT NULL, title TEXT NOT NULL,
                    deps_json TEXT NOT NULL, state TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL,
                    lease_token TEXT, lease_until REAL NOT NULL DEFAULT 0,
                    output TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (plan_id, step_id)
                );
                CREATE INDEX IF NOT EXISTS ix_durable_steps_state
                    ON durable_plan_steps(plan_id, state);
            """)

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=30000")
            yield db
        finally:
            db.close()

    @staticmethod
    def _check_owner(db: sqlite3.Connection, plan_id: str, owner_id: str) -> str:
        row = db.execute("SELECT owner_id,state FROM durable_plans WHERE plan_id=?", (plan_id,)).fetchone()
        if not row:
            raise KeyError("Plan not found")
        if row["owner_id"] != owner_id:
            raise PermissionError("Plan belongs to another owner")
        return str(row["state"])

    @staticmethod
    def _status(db: sqlite3.Connection, plan_id: str) -> None:
        states = [r[0] for r in db.execute("SELECT state FROM durable_plan_steps WHERE plan_id=?", (plan_id,))]
        if all(s == "done" for s in states):
            state = "completed"
        elif all(s in {"done", "failed", "blocked", "cancelled"} for s in states):
            # A failed branch must not prevent unrelated queued/running steps.
            state = "failed"
        else:
            state = "active"
        db.execute("UPDATE durable_plans SET state=? WHERE plan_id=? AND state NOT IN ('paused','cancelled')", (state, plan_id))

    def create(self, owner_id: str, steps: list[dict[str, Any]], *, plan_id: str | None = None) -> str:
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise PlanError("Owner is required")
        tasks = _validate_steps(steps)
        ident = plan_id or uuid.uuid4().hex
        if not isinstance(ident, str) or not _NAME.fullmatch(ident):
            raise PlanError("Invalid plan ID")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO durable_plans(plan_id,owner_id,state,created_at) VALUES(?,?,?,?)",
                       (ident, owner_id, "active", time.time()))
            db.executemany("INSERT INTO durable_plan_steps(plan_id,step_id,title,deps_json,state,max_attempts) "
                           "VALUES(?,?,?,?,'queued',?)",
                           [(ident, s["id"], s["title"], json.dumps(s["depends_on"]), s["max_attempts"]) for s in tasks])
            db.commit()
        return ident

    def claim(self, plan_id: str, owner_id: str, *, limit: int = 4,
              lease_seconds: int = 90, now: float | None = None) -> list[dict[str, Any]]:
        if not 1 <= limit <= 8 or not 1 <= lease_seconds <= 3600:
            raise PlanError("Invalid claim limits")
        clock = time.time() if now is None else float(now)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self._check_owner(db, plan_id, owner_id)
            if state not in {"active"}:
                db.commit()
                return []
            db.execute("UPDATE durable_plan_steps SET state='queued',lease_token=NULL,lease_until=0 "
                       "WHERE plan_id=? AND state='running' AND lease_until<=? AND attempt<max_attempts",
                       (plan_id, clock))
            db.execute("UPDATE durable_plan_steps SET state='failed',lease_token=NULL,lease_until=0 "
                       "WHERE plan_id=? AND state='running' AND lease_until<=? AND attempt>=max_attempts",
                       (plan_id, clock))
            rows = db.execute("SELECT * FROM durable_plan_steps WHERE plan_id=? ORDER BY rowid", (plan_id,)).fetchall()
            states = {row["step_id"]: row["state"] for row in rows}
            # Propagate terminal dependency failures before scheduling successors.
            changed = True
            while changed:
                changed = False
                for row in rows:
                    ident = row["step_id"]
                    if states[ident] != "queued":
                        continue
                    deps = json.loads(row["deps_json"])
                    if any(states[d] in {"failed", "blocked", "cancelled"} for d in deps):
                        states[ident] = "blocked"
                        db.execute("UPDATE durable_plan_steps SET state='blocked' WHERE plan_id=? AND step_id=?",
                                   (plan_id, ident))
                        changed = True
            claims: list[dict[str, Any]] = []
            for row in rows:
                ident = row["step_id"]
                if states[ident] != "queued" or len(claims) >= limit:
                    continue
                if not all(states[d] == "done" for d in json.loads(row["deps_json"])):
                    continue
                token = uuid.uuid4().hex
                db.execute("UPDATE durable_plan_steps SET state='running', attempt=attempt+1, "
                           "lease_token=?,lease_until=? WHERE plan_id=? AND step_id=? AND state='queued'",
                           (token, clock + lease_seconds, plan_id, ident))
                states[ident] = "running"
                claims.append({"id": ident, "title": row["title"], "token": token,
                               "attempt": row["attempt"] + 1})
            self._status(db, plan_id)
            db.commit()
            return claims

    def complete(self, plan_id: str, owner_id: str, step_id: str, token: str, *,
                 success: bool, output: str = "", now: float | None = None) -> bool:
        clock = time.time() if now is None else float(now)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self._check_owner(db, plan_id, owner_id)
            if state == "cancelled":
                db.commit()
                return False
            row = db.execute("SELECT attempt,max_attempts FROM durable_plan_steps "
                             "WHERE plan_id=? AND step_id=? AND state='running' AND lease_token=? AND lease_until>?",
                             (plan_id, step_id, token, clock)).fetchone()
            if row is None:
                db.commit()
                return False
            new_state = "done" if success else ("failed" if row["attempt"] >= row["max_attempts"] else "queued")
            db.execute("UPDATE durable_plan_steps SET state=?,output=?,lease_token=NULL,lease_until=0 "
                       "WHERE plan_id=? AND step_id=? AND lease_token=?",
                       (new_state, str(output)[:4096], plan_id, step_id, token))
            if new_state == "failed":
                # Mark descendants as blocked in the same transaction so the
                # final snapshot never retains impossible queued successors.
                changed = True
                while changed:
                    changed = False
                    rows = db.execute("SELECT step_id,deps_json,state FROM durable_plan_steps WHERE plan_id=?", (plan_id,)).fetchall()
                    states = {r["step_id"]: r["state"] for r in rows}
                    for item in rows:
                        if item["state"] != "queued":
                            continue
                        if any(states[dep] in {"failed", "blocked", "cancelled"} for dep in json.loads(item["deps_json"])):
                            db.execute("UPDATE durable_plan_steps SET state='blocked' WHERE plan_id=? AND step_id=?", (plan_id, item["step_id"]))
                            changed = True
            self._status(db, plan_id)
            db.commit()
            return True

    def heartbeat(self, plan_id: str, owner_id: str, step_id: str, token: str,
                  *, lease_seconds: int = 90, now: float | None = None) -> bool:
        """Extend an active claim; revoked and expired tokens never revive."""
        if not 1 <= lease_seconds <= 3600:
            raise PlanError("Invalid lease")
        clock = time.time() if now is None else float(now)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self._check_owner(db, plan_id, owner_id)
            changed = db.execute(
                "UPDATE durable_plan_steps SET lease_until=? "
                "WHERE plan_id=? AND step_id=? AND lease_token=? AND state='running' AND lease_until>?",
                (clock + lease_seconds, plan_id, step_id, token, clock),
            ) if state in {"active", "paused"} else None
            db.commit()
            return bool(changed and changed.rowcount == 1)

    def control(self, plan_id: str, owner_id: str, action: str) -> None:
        if action not in {"pause", "resume", "cancel"}:
            raise PlanError("Invalid action")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self._check_owner(db, plan_id, owner_id)
            if action == "pause" and state == "active":
                db.execute("UPDATE durable_plans SET state='paused' WHERE plan_id=?", (plan_id,))
            elif action == "resume" and state == "paused":
                db.execute("UPDATE durable_plans SET state='active' WHERE plan_id=?", (plan_id,))
                self._status(db, plan_id)
            elif action == "cancel" and state in {"active", "paused"}:
                db.execute("UPDATE durable_plans SET state='cancelled' WHERE plan_id=?", (plan_id,))
                db.execute("UPDATE durable_plan_steps SET state='cancelled',lease_token=NULL,lease_until=0 "
                           "WHERE plan_id=? AND state IN ('queued','running')", (plan_id,))
            db.commit()

    def snapshot(self, plan_id: str, owner_id: str) -> dict[str, Any]:
        with self._connection() as db:
            state = self._check_owner(db, plan_id, owner_id)
            rows = db.execute("SELECT step_id,title,deps_json,state,attempt,output FROM durable_plan_steps "
                              "WHERE plan_id=? ORDER BY rowid", (plan_id,)).fetchall()
        return {"id": plan_id, "state": state, "steps": [
            {"id": r["step_id"], "title": r["title"], "depends_on": json.loads(r["deps_json"]),
             "state": r["state"], "attempt": r["attempt"], "output": r["output"]} for r in rows]}
