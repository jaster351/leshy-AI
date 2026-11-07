from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from urllib.parse import urlparse

from .storage import Storage, utcnow

PRIMARY_TRUSTED_HOSTS = {"docs.python.org", "learn.microsoft.com", "developer.mozilla.org", "docs.github.com"}
ACADEMIC_TRUSTED_HOSTS = {
    "arxiv.org", "pubmed.ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov", "nature.com", "science.org", "plos.org",
    "biorxiv.org", "medrxiv.org", "jmlr.org", "openreview.net", "ieeexplore.ieee.org", "dl.acm.org",
    "journals.aps.org", "frontiersin.org", "royalsocietypublishing.org", "copernicus.org",
}
DEFAULT_DYNAMIC_TTL_SECONDS = 900


def normalize_claim(text: str) -> str:
    return " ".join(text.split()).strip()


def fingerprint_claim(text: str) -> str:
    return sha256(normalize_claim(text).lower().encode()).hexdigest()


def source_weight(url: str) -> float:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if not host:
        return .20
    if host.startswith("www."):
        host = host[4:]
    if host.endswith(".gov") or host.endswith(".edu"):
        return .92
    if host in ACADEMIC_TRUSTED_HOSTS or any(host.endswith("." + domain) for domain in ACADEMIC_TRUSTED_HOSTS):
        # Высокий provenance, но одиночная статья/препринт не должна стать trusted
        # только по домену. При .88 verify_topic потребует независимое подтверждение.
        return .88
    if host in PRIMARY_TRUSTED_HOSTS:
        return .90
    if host.startswith("docs.") or host.startswith("developer."):
        return .75
    if host == "github.com" or host.endswith(".github.com"):
        return .64
    if host == "wikidata.org" or host.endswith(".wikidata.org"):
        return .76
    if host == "wikipedia.org" or host.endswith(".wikipedia.org"):
        return .58
    if host in {"youtube.com", "youtu.be"}:
        return .50
    return .52


def is_fresh(row: dict, *, now: datetime | None = None) -> bool:
    if str(row.get("data_class") or "static") != "dynamic":
        return True
    checked_at = row.get("checked_at")
    ttl = row.get("ttl_seconds")
    if not checked_at or ttl is None:
        return False
    try:
        checked = datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        return 0 <= (current - checked).total_seconds() <= max(0, int(ttl))
    except (TypeError, ValueError, OverflowError):
        return False


class KnowledgeBase:
    def __init__(self, storage: Storage, min_trusted_confidence: float = .68, vector_index=None):
        self.storage = storage
        self.min_trusted_confidence = min_trusted_confidence
        self.vector_index = vector_index

    def add_candidate(
        self, *, topic: str, claim: str, source_url: str, source_title: str | None = None,
        source_type: str = "web", metadata_json: str | dict = "{}", data_class: str = "static",
        checked_at: str | None = None, ttl_seconds: int | None = None,
    ) -> int:
        if data_class not in {"static", "dynamic"}:
            raise ValueError("data_class must be 'static' or 'dynamic'")
        if data_class == "dynamic":
            checked_at = checked_at or utcnow()
            ttl_seconds = DEFAULT_DYNAMIC_TTL_SECONDS if ttl_seconds is None else max(0, int(ttl_seconds))
        else:
            checked_at = None
            ttl_seconds = None
        claim = normalize_claim(claim)
        fp = fingerprint_claim(claim)
        if isinstance(metadata_json, dict):
            metadata_json = json.dumps(metadata_json, ensure_ascii=False, sort_keys=True)
        audit_event = "knowledge.candidate_created"
        audit_payload = {"topic": topic, "source_url": source_url, "source_type": source_type, "data_class": data_class}
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT id FROM knowledge WHERE fingerprint=? AND source_url=? ORDER BY version DESC LIMIT 1",
                (fp, source_url),
            ).fetchone()
            if row:
                kid = int(row["id"])
                if data_class != "dynamic":
                    return kid
                conn.execute(
                    "UPDATE knowledge SET data_class='dynamic',checked_at=?,ttl_seconds=?,captured_at=?,source_title=?,source_type=?,metadata_json=? WHERE id=?",
                    (checked_at, ttl_seconds, utcnow(), source_title, source_type, str(metadata_json), kid),
                )
                audit_event = "knowledge.dynamic_rechecked"
                audit_payload = {"id": kid, "topic": topic, "source_url": source_url, "checked_at": checked_at, "ttl_seconds": ttl_seconds}
            else:
                cur = conn.execute(
                    "INSERT INTO knowledge(topic,claim,source_url,source_title,source_type,captured_at,confidence,status,fingerprint,metadata_json,data_class,checked_at,ttl_seconds) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (topic, claim, source_url, source_title, source_type, utcnow(), source_weight(source_url), "candidate", fp, str(metadata_json), data_class, checked_at, ttl_seconds),
                )
                kid = int(cur.lastrowid)
                audit_payload["id"] = kid
        # Audit writes through a separate connection, so it must happen only after
        # the knowledge transaction is committed to avoid SQLite writer deadlocks.
        self.storage.audit(audit_event, audit_payload)
        if self.vector_index and audit_event == "knowledge.candidate_created":
            self.vector_index.upsert("knowledge", kid, f"{topic}\n{claim}")
        return kid

    def counts(self) -> dict[str, int]:
        with self.storage.connection() as conn:
            rows = conn.execute("SELECT status, count(*) n FROM knowledge GROUP BY status").fetchall()
        by_status = {str(row["status"]): int(row["n"]) for row in rows}
        total = sum(by_status.values())
        candidate = by_status.get("candidate", 0)
        trusted = by_status.get("trusted", 0)
        return {
            "knowledge_total": total, "candidate": candidate, "trusted": trusted, "total": total,
            "conflict": by_status.get("conflict", 0), "stale": by_status.get("stale", 0),
            "rejected": by_status.get("rejected", 0),
        }

    def verify_topic(self, topic: str) -> dict[str, int]:
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT source_url,fingerprint,confidence FROM knowledge WHERE topic=? AND status IN ('candidate','trusted','conflict')",
                (topic,),
            ).fetchall()
            groups = defaultdict(list)
            for row in rows:
                groups[str(row["fingerprint"])].append(row)
            trusted = candidates = 0
            for fp, group in groups.items():
                hosts = {urlparse(str(row["source_url"])).hostname for row in group if urlparse(str(row["source_url"])).hostname}
                best = max(float(row["confidence"]) for row in group)
                confidence = min(.99, best + min(.21, max(0, len(hosts) - 1) * .105))
                status = "trusted" if confidence >= self.min_trusted_confidence and (len(hosts) >= 2 or best >= .90) else "candidate"
                conn.execute(
                    "UPDATE knowledge SET confidence=?,status=?,verified_at=? WHERE fingerprint=? AND topic=?",
                    (confidence, status, utcnow() if status == "trusted" else None, fp, topic),
                )
                trusted += status == "trusted"
                candidates += status == "candidate"
        self.storage.audit("knowledge.topic_verified", {"topic": topic, "trusted_groups": trusted, "candidate_groups": candidates})
        return {"trusted": int(trusted), "candidate": int(candidates), "conflict": 0}

    def _search_rows(self, query: str, rows, limit: int) -> list[dict]:
        # Раньше confidence=.90 сам по себе давал score>.5 даже при НУЛЕ
        # совпадений. Любой unrelated trusted-факт подавлял интернет-поиск.
        stop = {
            "какая", "какой", "какие", "какого", "что", "такое", "кто",
            "где", "когда", "почему", "сколько", "расскажи", "объясни",
            "скажи", "пожалуйста", "информация", "данные", "сегодня",
            "сейчас", "текущая", "актуальная", "найди", "проверь",
            "what", "which", "who", "where", "when", "please", "about",
            "today", "current", "latest",
        }
        terms = [
            term for term in re.findall(r"[0-9a-zа-яё]{3,}", str(query).lower())
            if term not in stop
        ][:12]
        if not terms:
            return []
        exact_ids = re.findall(r"\bCVE-\d{4}-\d{4,}\b|\bGHSA-[0-9a-z-]+\b", query, re.I)
        usable = [dict(row) for row in rows if is_fresh(dict(row))]
        by_id = {int(row["id"]): row for row in usable}
        scored = {}
        for row in usable:
            haystack = f"{row['topic']} {row['claim']}".lower()
            if exact_ids and any(identifier.lower() not in haystack for identifier in exact_ids):
                continue
            matches = sum(1 for term in terms if term in haystack)
            if matches == 0:
                continue
            score = matches + float(row["confidence"])
            scored[int(row["id"])] = (score, row)
        if self.vector_index:
            for hit in self.vector_index.search("knowledge", query, limit=limit * 3):
                item = by_id.get(hit.object_id)
                if not item:
                    continue
                if exact_ids and any(identifier.lower() not in
                                     f"{item['topic']} {item['claim']}".lower() for identifier in exact_ids):
                    continue
                if hit.score >= .62:
                    scored[hit.object_id] = (scored.get(hit.object_id, (0, item))[0] + max(0, hit.score) * 2, item)
        return [row for _, row in sorted(scored.values(), key=lambda item: item[0], reverse=True)[:limit]]

    def search(self, query: str, *, trusted_only: bool = True, limit: int = 8) -> list[dict]:
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge WHERE status='trusted' ORDER BY confidence DESC,id DESC LIMIT 500"
                if trusted_only else "SELECT * FROM knowledge ORDER BY confidence DESC,id DESC LIMIT 500"
            ).fetchall()
        return self._search_rows(query, rows, limit)

    def search_candidates(self, query: str, *, limit: int = 4) -> list[dict]:
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge WHERE status='candidate' ORDER BY captured_at DESC,confidence DESC,id DESC LIMIT 500"
            ).fetchall()
        return self._search_rows(query, rows, limit)
