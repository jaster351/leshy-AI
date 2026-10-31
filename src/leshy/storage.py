from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL DEFAULT 'standalone',title TEXT NOT NULL DEFAULT '',title_source TEXT NOT NULL DEFAULT 'auto',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,role TEXT NOT NULL CHECK(role IN ('system','user','assistant','tool')),content TEXT NOT NULL,created_at TEXT NOT NULL,edited_at TEXT);
CREATE INDEX IF NOT EXISTS ix_messages_session_id ON messages(session_id,id);
CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY AUTOINCREMENT,owner_id TEXT NOT NULL DEFAULT 'shared',kind TEXT NOT NULL,content TEXT NOT NULL,importance REAL NOT NULL DEFAULT 0.5,source TEXT,created_at TEXT NOT NULL,expires_at TEXT,fingerprint TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS knowledge(id INTEGER PRIMARY KEY AUTOINCREMENT,topic TEXT NOT NULL,claim TEXT NOT NULL,source_url TEXT NOT NULL,source_title TEXT,source_type TEXT NOT NULL DEFAULT 'web',captured_at TEXT NOT NULL,verified_at TEXT,confidence REAL NOT NULL DEFAULT 0,status TEXT NOT NULL CHECK(status IN ('candidate','trusted','conflict','rejected','stale')),version INTEGER NOT NULL DEFAULT 1,fingerprint TEXT NOT NULL,supersedes_id INTEGER REFERENCES knowledge(id),metadata_json TEXT NOT NULL DEFAULT '{}',data_class TEXT NOT NULL DEFAULT 'static',checked_at TEXT,ttl_seconds INTEGER,UNIQUE(fingerprint,source_url,version));
CREATE INDEX IF NOT EXISTS ix_knowledge_topic ON knowledge(topic,status,confidence DESC);
CREATE INDEX IF NOT EXISTS ix_knowledge_fingerprint ON knowledge(fingerprint);
CREATE TABLE IF NOT EXISTS research_tasks(id INTEGER PRIMARY KEY AUTOINCREMENT,query TEXT NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('queued','running','done','failed')),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT);
CREATE INDEX IF NOT EXISTS ix_research_status ON research_tasks(status,id);
CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT,event_type TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS embeddings(object_type TEXT NOT NULL,object_id INTEGER NOT NULL,model TEXT NOT NULL,vector_json TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(object_type,object_id,model));
CREATE TABLE IF NOT EXISTS model_versions(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,artifact TEXT,status TEXT NOT NULL CHECK(status IN ('candidate','active','rejected','archived')),eval_json TEXT NOT NULL DEFAULT '[]',created_at TEXT NOT NULL,activated_at TEXT,parent_id INTEGER REFERENCES model_versions(id));
CREATE INDEX IF NOT EXISTS ix_model_versions_status ON model_versions(status,id);
CREATE TABLE IF NOT EXISTS tool_approvals(id INTEGER PRIMARY KEY AUTOINCREMENT,owner_id TEXT NOT NULL,session_id TEXT,tool_name TEXT NOT NULL,arguments_json TEXT NOT NULL,fingerprint TEXT NOT NULL,risk TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','consumed','expired')),created_at TEXT NOT NULL,expires_at TEXT NOT NULL,decided_at TEXT,decided_by TEXT,decision_reason TEXT,consumed_at TEXT);
CREATE INDEX IF NOT EXISTS ix_tool_approvals_lookup ON tool_approvals(owner_id,tool_name,fingerprint,status,expires_at);
CREATE INDEX IF NOT EXISTS ix_tool_approvals_status ON tool_approvals(status,id DESC);
CREATE TABLE IF NOT EXISTS learning_activity(id INTEGER PRIMARY KEY CHECK(id=1),status TEXT NOT NULL CHECK(status IN ('idle','learning')),kind TEXT NOT NULL DEFAULT '',title TEXT NOT NULL DEFAULT '',url TEXT NOT NULL DEFAULT '',site TEXT NOT NULL DEFAULT '',detail TEXT NOT NULL DEFAULT '',started_at TEXT,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learning_history(id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,title TEXT NOT NULL,url TEXT NOT NULL DEFAULT '',site TEXT NOT NULL DEFAULT '',detail TEXT NOT NULL DEFAULT '',started_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_learning_history_started ON learning_history(started_at DESC,id DESC);
CREATE TABLE IF NOT EXISTS sync_packages(package_hash TEXT PRIMARY KEY,source TEXT NOT NULL,remote_instance TEXT NOT NULL DEFAULT '',imported_count INTEGER NOT NULL DEFAULT 0,imported_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_sync_packages_imported ON sync_packages(imported_at DESC);
CREATE TABLE IF NOT EXISTS user_tasks(id INTEGER PRIMARY KEY AUTOINCREMENT,owner_id TEXT NOT NULL,title TEXT NOT NULL,details TEXT NOT NULL DEFAULT '',status TEXT NOT NULL CHECK(status IN ('open','in_progress','done','cancelled')),priority INTEGER NOT NULL DEFAULT 0,due_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_user_tasks_owner_status ON user_tasks(owner_id,status,priority DESC,updated_at DESC);
CREATE TABLE IF NOT EXISTS user_preferences(owner_id TEXT NOT NULL,key TEXT NOT NULL,value_json TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(owner_id,key));
CREATE INDEX IF NOT EXISTS ix_user_preferences_owner ON user_preferences(owner_id,key);
"""


class Storage:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def migrate(self) -> None:
        with self.connection() as conn:
            conn.executescript(SCHEMA)
            self._ensure_column(conn, "sessions", "owner_id", "TEXT NOT NULL DEFAULT 'standalone'")
            self._ensure_column(conn, "sessions", "title", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "sessions", "title_source", "TEXT NOT NULL DEFAULT 'auto'")
            self._ensure_column(conn, "messages", "edited_at", "TEXT")
            self._ensure_column(conn, "memories", "owner_id", "TEXT NOT NULL DEFAULT 'shared'")
            self._ensure_column(conn, "knowledge", "data_class", "TEXT NOT NULL DEFAULT 'static'")
            self._ensure_column(conn, "knowledge", "checked_at", "TEXT")
            self._ensure_column(conn, "knowledge", "ttl_seconds", "INTEGER")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_sessions_owner_updated ON sessions(owner_id,updated_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_memories_owner_importance ON memories(owner_id,importance DESC,id DESC)")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def audit(self, event_type: str, payload: dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO audit_log(event_type,payload_json,created_at) VALUES(?,?,?)",
                (event_type, json.dumps(payload, ensure_ascii=False, sort_keys=True), utcnow()),
            )
