"""Durable dependency graphs for autonomous tasks, without executing tools.

SQLite leases protect a step from being completed by an obsolete worker.
Plan content stays scoped to the owner; no LLM or network work occurs here.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
from typing import Any


_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SAFE_READ_TOOLS = frozenset({"knowledge.search", "runtime.version", "runtime.platform"})


def _safe_read_spec(step: dict[str, Any]) -> tuple[str, str]:
    """Persist only strictly validated read actions; text steps remain inert."""
    tool = step.get("tool", "")
    arguments = step.get("arguments", {})
    if tool == "" and arguments == {}:
        return "", "{}"
    if not isinstance(tool, str) or tool not in _SAFE_READ_TOOLS:
        raise PlanError("Only explicitly approved read-only plan tools are supported")
    if not isinstance(arguments, dict):
        raise PlanError("Tool arguments must be a JSON object")
    if tool == "knowledge.search":
        query = arguments.get("query")
        if set(arguments) != {"query"} or not isinstance(query, str) or not 3 <= len(query.strip()) <= 300:
            raise PlanError("knowledge.search requires a query of 3–300 characters")
        arguments = {"query": " ".join(query.split())}
    elif arguments:
        raise PlanError("Runtime read tools take no arguments")
    try:
        serialized = json.dumps(arguments, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise PlanError("Tool arguments must be JSON-compatible") from None
    if len(serialized.encode("utf-8")) > 4096:
        raise PlanError("Tool arguments exceed 4096 bytes")
    return tool, serialized



class PlanError(ValueError):
    pass


def _validate_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(steps, list) or not steps:
        raise PlanError("Plan must contain at least one step")
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
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise PlanError("Invalid dependencies")
        if len(deps) != len(set(deps)):
            raise PlanError("Duplicate dependency")
        attempts = step.get("max_attempts", 3)
        if type(attempts) is not int or not 1 <= attempts <= 8:
            raise PlanError("max_attempts must be 1–8")
        tool, args_json = _safe_read_spec(step)
        ids.add(ident)
        normalized.append({"id": ident, "title": title.strip(), "depends_on": deps,
                           "max_attempts": attempts, "tool": tool, "args_json": args_json})
    # O(steps + dependencies), not quadratic rescans for long DAGs.
    # Batch sizes are constrained at execution by available workers/resources,
    # not by a hard-coded limit on the number of planned steps.
    indegree = {step["id"]: len(step["depends_on"]) for step in normalized}
    children: dict[str, list[str]] = {ident: [] for ident in indegree}
    for step in normalized:
        for dep in step["depends_on"]:
            if dep not in indegree:
                raise PlanError("Unknown dependency")
            children[dep].append(step["id"])
    ready = deque(ident for ident, count in indegree.items() if count == 0)
    visited = 0
    while ready:
        ident = ready.popleft()
        visited += 1
        for child in children[ident]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if visited != len(normalized):
        raise PlanError("Circular dependencies")
    return normalized


def _block_failed_descendants(db: sqlite3.Connection, plan_id: str) -> None:
    """Cascade terminal dependency failures in O(steps + edges)."""
    if db.execute(
        "SELECT 1 FROM durable_plan_steps "
        "WHERE plan_id=? AND state IN ('failed','blocked','cancelled') LIMIT 1",
        (plan_id,),
    ).fetchone() is None:
        return
    rows = db.execute(
        "SELECT step_id,deps_json,state FROM durable_plan_steps WHERE plan_id=?",
        (plan_id,),
    ).fetchall()
    states = {row["step_id"]: row["state"] for row in rows}
    failed = deque(
        row["step_id"] for row in rows
        if row["state"] in {"failed", "blocked", "cancelled"}
    )
    children: dict[str, list[str]] = {}
    for row in rows:
        if row["state"] != "queued":
            continue
        for dependency in json.loads(row["deps_json"]):
            children.setdefault(dependency, []).append(row["step_id"])
    pending: list[tuple[str, str]] = []
    while failed:
        parent = failed.popleft()
        for child in children.get(parent, ()):
            if states[child] == "queued":
                states[child] = "blocked"
                pending.append((plan_id, child))
                failed.append(child)
    if pending:
        db.executemany(
            "UPDATE durable_plan_steps SET state='blocked' "
            "WHERE plan_id=? AND step_id=? AND state='queued'",
            pending,
        )



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
                    tool_name TEXT NOT NULL DEFAULT '',
                    args_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (plan_id, step_id)
                );
                CREATE INDEX IF NOT EXISTS ix_durable_steps_state
                    ON durable_plan_steps(plan_id, state);
            """)
            # Preserve plans created by older builds; migrate without deleting
            # in-flight leases or rewriting completed step outputs.
            db.execute("BEGIN IMMEDIATE")
            cols = {row["name"] for row in db.execute("PRAGMA table_info(durable_plan_steps)")}
            if "tool_name" not in cols:
                db.execute("ALTER TABLE durable_plan_steps ADD COLUMN tool_name TEXT NOT NULL DEFAULT ''")
            if "args_json" not in cols:
                db.execute("ALTER TABLE durable_plan_steps ADD COLUMN args_json TEXT NOT NULL DEFAULT '{}'")
            db.commit()

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
            db.executemany("INSERT INTO durable_plan_steps("
                           "plan_id,step_id,title,deps_json,state,max_attempts,tool_name,args_json) "
                           "VALUES(?,?,?,?,'queued',?,?,?)",
                           [(ident, s["id"], s["title"], json.dumps(s["depends_on"]),
                             s["max_attempts"], s["tool"], s["args_json"]) for s in tasks])
            db.commit()
        return ident

    def claim(self, plan_id: str, owner_id: str, *, limit: int = 4,
              lease_seconds: int = 90, now: float | None = None,
              tool_only: bool = False) -> list[dict[str, Any]]:
        if (type(limit) is not int or limit < 1 or type(lease_seconds) is not int
                or not 1 <= lease_seconds <= 3600 or type(tool_only) is not bool):
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
            _block_failed_descendants(db, plan_id)
            rows = db.execute("SELECT * FROM durable_plan_steps WHERE plan_id=? ORDER BY rowid", (plan_id,)).fetchall()
            states = {row["step_id"]: row["state"] for row in rows}
            claims: list[dict[str, Any]] = []
            for row in rows:
                ident = row["step_id"]
                if states[ident] != "queued" or len(claims) >= limit:
                    continue
                # A trusted automatic reader must not claim human-only text steps.
                if tool_only and not row["tool_name"]:
                    continue
                if not all(states[d] == "done" for d in json.loads(row["deps_json"])):
                    continue
                token = uuid.uuid4().hex
                db.execute("UPDATE durable_plan_steps SET state='running', attempt=attempt+1, "
                           "lease_token=?,lease_until=? WHERE plan_id=? AND step_id=? AND state='queued'",
                           (token, clock + lease_seconds, plan_id, ident))
                states[ident] = "running"
                claims.append({"id": ident, "title": row["title"], "token": token,
                               "attempt": row["attempt"] + 1,
                               "tool": row["tool_name"],
                               "arguments": json.loads(row["args_json"])})
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
                _block_failed_descendants(db, plan_id)
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
            rows = db.execute("SELECT step_id,title,deps_json,state,attempt,output,tool_name,args_json "
                              "FROM durable_plan_steps WHERE plan_id=? ORDER BY rowid", (plan_id,)).fetchall()
        return {"id": plan_id, "state": state, "steps": [
            {"id": r["step_id"], "title": r["title"], "depends_on": json.loads(r["deps_json"]),
             "state": r["state"], "attempt": r["attempt"], "output": r["output"],
             "tool": r["tool_name"], "arguments": json.loads(r["args_json"])}
            for r in rows]}
