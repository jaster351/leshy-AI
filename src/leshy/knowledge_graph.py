from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from typing import Any

from .diagnostics import event, problem
from .storage import utcnow


_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_-]{4,}", re.UNICODE)
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
        self._last_sync = 0.0
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
                    weight=MAX(knowledge_graph_edges.weight,excluded.weight),
                    evidence=excluded.evidence,
                    updated_at=excluded.updated_at
                """,
                (owner_id, concept_id, fact_id, "mentions", float(weight), text[:1000], now),
            )

    def sync(self, *, owner_id: str | None = None, force: bool = False) -> dict[str, int]:
        now = time.monotonic()
        if not force and now - self._last_sync < 30.0:
            return {"knowledge": 0, "memories": 0}
        self._last_sync = now
        knowledge_count = 0
        memory_count = 0
        try:
            with self.storage.connection() as conn:
                rows = conn.execute(
                    """
                    SELECT id,claim,source_url,confidence
                    FROM knowledge
                    WHERE status='trusted'
                    ORDER BY confidence DESC,id DESC
                    LIMIT 1200
                    """
                ).fetchall()
                for row in rows:
                    text = str(row["claim"] or "").strip()
                    if not text:
                        continue
                    self._link_fact(
                        conn,
                        owner_id="shared",
                        fact_kind="knowledge",
                        fact_key=f"knowledge:{int(row['id'])}",
                        text=text,
                        source=str(row["source_url"] or ""),
                        weight=max(0.2, min(float(row["confidence"] or 0.8), 1.0)),
                    )
                    knowledge_count += 1

                if owner_id:
                    memory_rows = conn.execute(
                        """
                        SELECT id,content,importance,source
                        FROM memories
                        WHERE owner_id=?
                        ORDER BY importance DESC,id DESC
                        LIMIT 500
                        """,
                        (str(owner_id)[:200],),
                    ).fetchall()
                    for row in memory_rows:
                        text = str(row["content"] or "").strip()
                        if not text:
                            continue
                        self._link_fact(
                            conn,
                            owner_id=str(owner_id)[:200],
                            fact_kind="memory",
                            fact_key=f"memory:{int(row['id'])}",
                            text=text,
                            source=str(row["source"] or "memory"),
                            weight=max(0.2, min(float(row["importance"] or 0.6), 1.0)),
                        )
                        memory_count += 1
        except Exception as exc:
            problem("knowledge_graph_sync_failed", str(exc), owner_id=str(owner_id or ""))
            return {"knowledge": 0, "memories": 0}
        event(
            "knowledge_graph_synced",
            owner_id=str(owner_id or ""),
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
        try:
            with self.storage.connection() as conn:
                for scope in ("shared", str(owner_id or "")[:200]):
                    if not scope:
                        continue
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
                            SELECT n.id,n.kind,n.label,n.payload_json,e.weight
                            FROM knowledge_graph_edges e
                            JOIN knowledge_graph_nodes n ON n.id=e.target_id
                            WHERE e.owner_id=? AND e.source_id=?
                            ORDER BY e.weight DESC,n.id DESC
                            LIMIT 24
                            """,
                            (scope, int(concept_row["id"])),
                        ).fetchall()
                        for neighbor in neighbors:
                            kind = str(neighbor["kind"])
                            if kind not in {"knowledge", "memory"}:
                                continue
                            text = str(neighbor["label"] or "")
                            payload = {}
                            try:
                                payload = json.loads(str(neighbor["payload_json"] or "{}"))
                            except Exception:
                                payload = {}
                            key = (scope, int(neighbor["id"]))
                            previous = facts.get(key)
                            base = float(neighbor["weight"] or 0.0)
                            score = base + 0.22
                            related = {concept}
                            if previous:
                                score = previous.score + 0.22
                                related.update(previous.related_concepts)
                            facts[key] = GraphFact(
                                kind=kind,
                                text=text,
                                score=score,
                                source=str(payload.get("source") or ""),
                                related_concepts=tuple(sorted(related)),
                            )
        except Exception as exc:
            problem("knowledge_graph_search_failed", str(exc), owner_id=str(owner_id or ""))
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
                scopes = ("shared", str(owner_id)[:200])
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
