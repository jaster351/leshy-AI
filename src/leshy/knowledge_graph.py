from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import threading
import time
from typing import Any

from .diagnostics import event, problem
from .knowledge import KnowledgeBase, is_fresh
from .memory_freshness import memory_is_fresh
from .storage import utcnow
from .text_integrity import corrupted_knowledge_row


_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_-]{4,}", re.UNICODE)
_SOURCE_KEY_RE = re.compile(r"(knowledge|memory):([1-9][0-9]{0,18})")
_SOURCE_BATCH_SIZE = 128
_STOPWORDS = {
    "который", "которая", "которые", "этого", "этот", "эта", "эти", "также", "может",
    "можно", "нужно", "будет", "были", "есть", "чтобы", "если", "когда", "после", "перед",
    "через", "между", "только", "очень", "более", "менее", "один", "одна", "одно", "данные",
    "пользователь", "пользователя", "система", "системы", "леший", "лешего", "работает", "работать",
    "from", "with", "that", "this", "have", "will", "into", "using",
}


def _concepts(text: str, *, limit: int = 12) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in _WORD_RE.findall(str(text or "")):
        token = raw.lower().strip("-_")
        if len(token) < 4 or token in _STOPWORDS or token.isdigit() or token in seen:
            continue
        seen.add(token)
        result.append(token)
        if len(result) >= limit:
            break
    return result


@dataclass(slots=True)
class GraphFact:
    kind: str
    text: str
    score: float
    source: str
    related_concepts: tuple[str, ...]

    def public(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "score": round(self.score, 4),
            "source": self.source,
            "related_concepts": list(self.related_concepts),
        }


class KnowledgeGraph:
    """Локальный GraphRAG-слой поверх знаний и памяти LeshyBrain.

    Граф хранит факты и пользовательские воспоминания как узлы, связывает их с
    концептами и при запросе возвращает не только прямые совпадения, но и соседние
    связанные факты. Внешняя БД и облачный API не требуются.
    """

    def __init__(self, storage):
        self.storage = storage
        self._last_sync_by_scope: dict[tuple[str, str], float] = {}
        self._sync_lock = threading.RLock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.storage.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_graph_nodes(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    node_key TEXT NOT NULL,
                    label TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_id,kind,node_key)
                );
                CREATE INDEX IF NOT EXISTS ix_knowledge_graph_nodes_owner_kind
                    ON knowledge_graph_nodes(owner_id,kind,node_key);
                CREATE TABLE IF NOT EXISTS knowledge_graph_edges(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL,
                    source_id INTEGER NOT NULL REFERENCES knowledge_graph_nodes(id) ON DELETE CASCADE,
                    target_id INTEGER NOT NULL REFERENCES knowledge_graph_nodes(id) ON DELETE CASCADE,
                    relation TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    evidence TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_id,source_id,target_id,relation)
                );
                CREATE INDEX IF NOT EXISTS ix_knowledge_graph_edges_source
                    ON knowledge_graph_edges(owner_id,source_id,weight DESC);
                CREATE INDEX IF NOT EXISTS ix_knowledge_graph_edges_target
                    ON knowledge_graph_edges(owner_id,target_id,weight DESC);
                """
            )

    def _upsert_node(
        self,
        conn,
        *,
        owner_id: str,
        kind: str,
        node_key: str,
        label: str,
        payload: dict[str, Any] | None = None,
    ) -> int:
        now = utcnow()
        conn.execute(
            """
            INSERT INTO knowledge_graph_nodes(owner_id,kind,node_key,label,payload_json,updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(owner_id,kind,node_key) DO UPDATE SET
                label=excluded.label,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (
                owner_id,
                kind,
                node_key,
                label,
                json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                now,
            ),
        )
        row = conn.execute(
            "SELECT id FROM knowledge_graph_nodes WHERE owner_id=? AND kind=? AND node_key=?",
            (owner_id, kind, node_key),
        ).fetchone()
        return int(row["id"])

    def _link_fact(
        self,
        conn,
        *,
        owner_id: str,
        fact_kind: str,
        fact_key: str,
        text: str,
        source: str,
        weight: float,
    ) -> None:
        fact_id = self._upsert_node(
            conn,
            owner_id=owner_id,
            kind=fact_kind,
            node_key=fact_key,
            label=text,
            payload={"source": source},
        )
        # Меняются только производные связи этого факта. Исходные записи
        # памяти/знаний и пользовательские отношения графа не удаляются.
        conn.execute(
            "DELETE FROM knowledge_graph_edges WHERE owner_id=? AND target_id=? AND relation='mentions'",
            (owner_id, fact_id),
        )
        now = utcnow()
        for concept in _concepts(text):
            concept_id = self._upsert_node(
                conn,
                owner_id=owner_id,
                kind="concept",
                node_key=concept,
                label=concept,
            )
            conn.execute(
                """
                INSERT INTO knowledge_graph_edges(owner_id,source_id,target_id,relation,weight,evidence,updated_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(owner_id,source_id,target_id,relation) DO UPDATE SET
                    weight=excluded.weight,
                    evidence=excluded.evidence,
                    updated_at=excluded.updated_at
                """,
                (owner_id, concept_id, fact_id, "mentions", float(weight), text[:1000], now),
            )

    def sync(self, *, owner_id: str | None = None, force: bool = False) -> dict[str, int]:
        owner = str(owner_id or "")
        scopes = [("knowledge", "shared")]
        if owner:
            scopes.append(("memory", owner))
        with self._sync_lock:
            now = time.monotonic()
            due = [scope for scope in scopes if force or scope not in self._last_sync_by_scope
                   or now - self._last_sync_by_scope[scope] >= 30.0]
            if not due:
                return {"knowledge": 0, "memories": 0}
            knowledge_count = 0
            memory_count = 0
            checked_now = datetime.now(timezone.utc)
            try:
                with self.storage.connection() as conn:
                    for kind, scope in due:
                        if kind == "knowledge":
                            rows = conn.execute(
                                "SELECT * FROM knowledge WHERE status='trusted' "
                                "ORDER BY confidence DESC,id DESC LIMIT 1200"
                            ).fetchall()
                            for raw in rows:
                                row = dict(raw)
                                text = str(row["claim"] or "").strip()
                                if (not text or not is_fresh(row, now=checked_now)
                                        or corrupted_knowledge_row(row)):
                                    continue
                                self._link_fact(
                                    conn, owner_id=scope, fact_kind=kind,
                                    fact_key=f"knowledge:{int(row['id'])}", text=text,
                                    source=str(row["source_url"] or ""),
                                    weight=max(0.2, min(float(0.8 if row["confidence"] is None else row["confidence"]), 1.0)),
                                )
                                knowledge_count += 1
                        else:
                            rows = conn.execute(
                                "SELECT id,content,importance,source,expires_at FROM memories "
                                "WHERE owner_id=? ORDER BY importance DESC,id DESC LIMIT 1000",
                                (scope,),
                            ).fetchall()
                            for row in rows:
                                text = str(row["content"] or "").strip()
                                if not text or not memory_is_fresh(row["expires_at"], now=checked_now):
                                    continue
                                self._link_fact(
                                    conn, owner_id=scope, fact_kind=kind,
                                    fact_key=f"memory:{int(row['id'])}", text=text,
                                    source=str(row["source"] or "memory"),
                                    weight=max(0.2, min(float(0.6 if row["importance"] is None else row["importance"]), 1.0)),
                                )
                                memory_count += 1
            except Exception as exc:
                problem("knowledge_graph_sync_failed", type(exc).__name__, owner_id=owner[:200])
                return {"knowledge": 0, "memories": 0}
            # Неудачный commit не должен блокировать немедленную новую попытку.
            completed = time.monotonic()
            for scope in due:
                self._last_sync_by_scope[scope] = completed
        event(
            "knowledge_graph_synced",
            owner_id=owner[:200],
            knowledge=knowledge_count,
            memories=memory_count,
        )
        return {"knowledge": knowledge_count, "memories": memory_count}

    def search(self, owner_id: str, query: str, *, limit: int = 7) -> list[GraphFact]:
        self.sync(owner_id=owner_id)
        query_concepts = _concepts(query, limit=16)
        if not query_concepts:
            return []
        facts: dict[tuple[str, int], GraphFact] = {}
        owner = str(owner_id or "")
        scopes = [("knowledge", "shared")]
        if owner:
            scopes.append(("memory", owner))
        try:
            with self.storage.connection() as conn:
                # Один снимок графа и его первичных источников, без нового Storage.
                conn.execute("BEGIN")
                candidates = []
                source_ids: dict[str, set[int]] = {"knowledge": set(), "memory": set()}
                for kind, scope in scopes:
                    placeholders = ",".join("?" for _ in query_concepts)
                    concept_rows = conn.execute(
                        f"""
                        SELECT id,node_key
                        FROM knowledge_graph_nodes
                        WHERE owner_id=? AND kind='concept' AND node_key IN ({placeholders})
                        """,
                        (scope, *query_concepts),
                    ).fetchall()
                    for concept_row in concept_rows:
                        concept = str(concept_row["node_key"])
                        neighbors = conn.execute(
                            """
                            SELECT n.id,n.kind,n.owner_id,n.node_key,n.label,e.weight
                            FROM knowledge_graph_edges e
                            JOIN knowledge_graph_nodes n ON n.id=e.target_id
                            WHERE e.owner_id=? AND e.source_id=? AND n.owner_id=?
                                  AND n.kind=? AND e.relation='mentions'
                            ORDER BY e.weight DESC,n.id DESC
                            LIMIT 24
                            """,
                            (scope, int(concept_row["id"]), scope, kind),
                        ).fetchall()
                        for neighbor in neighbors:
                            match = _SOURCE_KEY_RE.fullmatch(str(neighbor["node_key"]))
                            if not match or match.group(1) != kind:
                                continue
                            source_id = int(match.group(2))
                            if source_id > 9223372036854775807:
                                continue
                            source_ids[kind].add(source_id)
                            candidates.append((kind, scope, concept, dict(neighbor), source_id))

                # Не обрезать источники top-N: ранее найденный живой tail-id
                # тоже проверяется. Всего соседей <=16*24*2, параметры пачкой128.
                current: dict[str, dict[int, dict]] = {"knowledge": {}, "memory": {}}
                for kind, identifiers in source_ids.items():
                    identifiers = sorted(identifiers)
                    batch_size = _SOURCE_BATCH_SIZE - (1 if kind == "memory" else 0)
                    for start in range(0, len(identifiers), batch_size):
                        batch = identifiers[start:start + batch_size]
                        placeholders = ",".join("?" for _ in batch)
                        if kind == "knowledge":
                            rows = conn.execute(
                                f"SELECT * FROM knowledge WHERE status='trusted' AND id IN ({placeholders})",
                                batch,
                            ).fetchall()
                        else:
                            rows = conn.execute(
                                "SELECT id,owner_id,content,importance,source,expires_at FROM memories "
                                f"WHERE owner_id=? AND id IN ({placeholders})",
                                (owner, *batch),
                            ).fetchall()
                        current[kind].update((int(row["id"]), dict(row)) for row in rows)
                # Тот же полный запрос и ограничения CVE/версий/целостности,
                # что у KnowledgeBase. Конструктор и _search_rows не читают БД.
                knowledge_rows = list(current["knowledge"].values())
                accepted = KnowledgeBase(self.storage, vector_index=None)._search_rows(
                    query, knowledge_rows, len(knowledge_rows))
                current["knowledge"] = {int(row["id"]): row for row in accepted}
                checked_now = datetime.now(timezone.utc)
                for kind, scope, concept, neighbor, source_id in candidates:
                    source = current[kind].get(source_id)
                    if source is None:
                        continue
                    if kind == "memory":
                        if (source["owner_id"] != owner
                                or not memory_is_fresh(source["expires_at"], now=checked_now)):
                            continue
                        text = str(source["content"] or "").strip()
                        provenance = str(source["source"] or "memory")
                        base = max(0.2, min(float(0.6 if source["importance"] is None else source["importance"]), 1.0))
                    else:
                        if not is_fresh(source, now=checked_now):
                            continue
                        text = str(source["claim"] or "").strip()
                        provenance = str(source["source_url"] or "")
                        base = max(0.2, min(float(0.8 if source["confidence"] is None else source["confidence"]), 1.0))
                    if not text or neighbor["label"] != text or concept not in _concepts(text):
                        continue
                    key = (scope, int(neighbor["id"]))
                    previous = facts.get(key)
                    score = previous.score + 0.22 if previous else base + 0.22
                    related = {concept}
                    if previous:
                        related.update(previous.related_concepts)
                    facts[key] = GraphFact(kind=kind, text=text, score=score, source=provenance,
                                           related_concepts=tuple(sorted(related)))
        except Exception as exc:
            problem("knowledge_graph_search_failed", type(exc).__name__, owner_id=owner[:200])
            return []

        ordered = sorted(facts.values(), key=lambda item: (-item.score, item.kind, item.text))
        return ordered[: max(1, min(int(limit), 30))]

    def context(self, owner_id: str, query: str, *, limit: int = 7) -> str:
        rows = self.search(owner_id, query, limit=limit)
        if not rows:
            return ""
        return "\n".join(
            f"- {item.text}" + (f" [источник: {item.source}]" if item.source else "")
            for item in rows
        )

    def status(self, owner_id: str | None = None) -> dict[str, Any]:
        with self.storage.connection() as conn:
            if owner_id:
                scopes = ("shared", str(owner_id))
                nodes = int(conn.execute(
                    "SELECT COUNT(*) AS c FROM knowledge_graph_nodes WHERE owner_id IN (?,?)",
                    scopes,
                ).fetchone()["c"])
                edges = int(conn.execute(
                    "SELECT COUNT(*) AS c FROM knowledge_graph_edges WHERE owner_id IN (?,?)",
                    scopes,
                ).fetchone()["c"])
            else:
                nodes = int(conn.execute("SELECT COUNT(*) AS c FROM knowledge_graph_nodes").fetchone()["c"])
                edges = int(conn.execute("SELECT COUNT(*) AS c FROM knowledge_graph_edges").fetchone()["c"])
        return {"enabled": True, "local_only": True, "nodes": nodes, "edges": edges}


__all__ = ["KnowledgeGraph", "GraphFact"]
