"""Коллективная сверка факта происхождения Лешего в доверенном LAN-кластере.

Голоса узлов — диагностические свидетельства, а не источник полномочий.
Даже большинство не может изменить встроенное имя создателя или выдать роль.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
import re
import threading
import time
import unicodedata
from typing import Any, Mapping

from .identity_runtime import CREATOR_FULL_NAME
from .storage import utcnow


_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CACHE_KEY = "creator.identity.informational.v1"
_MIN_INTERVAL = 300.0


def creator_identity_digest() -> str:
    """Числовой отпечаток только канонического имени из кода сборки."""
    canonical = unicodedata.normalize("NFC", CREATOR_FULL_NAME).strip()
    return sha256(("leshy.creator.v1\0" + canonical).encode("utf-8")).hexdigest()


def summarize_creator_votes(local_digest: str, peer_votes: Mapping[str, str]) -> dict[str, Any]:
    """Один голос на наблюдаемый node_id; не считать это защитой от Sybil.

    Отсутствие ответа не превращается в голос. Победивший отпечаток никогда
    не переписывает первоисточник из исходников или серверные роли.
    """
    if not isinstance(local_digest, str) or not _DIGEST_RE.fullmatch(local_digest):
        raise ValueError("Неверный локальный отпечаток создателя")
    if not isinstance(peer_votes, Mapping):
        raise ValueError("Неверный набор ответов Федерации")
    valid: dict[str, str] = {}
    for raw_node, value in peer_votes.items():
        if (isinstance(raw_node, str) and 1 <= len(raw_node) <= 128
                and isinstance(value, str) and _DIGEST_RE.fullmatch(value)):
            valid[raw_node] = value
    counts = Counter([local_digest, *valid.values()])
    total = sum(counts.values())
    highest = max(counts.values())
    leaders = sorted(key for key, count in counts.items() if count == highest)
    leader = leaders[0] if len(leaders) == 1 and highest > total / 2 else None
    differing = sorted(node for node, vote in valid.items() if vote != local_digest)
    if not valid:
        status = "no_peer_votes"
    elif not differing:
        status = "consistent"
    elif leader is None:
        status = "conflict_no_majority"
    elif leader != local_digest:
        status = "local_outlier"
    else:
        status = "peer_divergence"
    return {
        "status": status,
        "local_digest": local_digest,
        "reported_peers": len(valid),
        "total_observed_nodes": total,
        "matching_local": counts[local_digest],
        "differing_peer_ids": differing,
        "distributions": [
            {"digest": digest, "count": count}
            for digest, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "largest_group": highest,
        "majority_digest": leader,
        "majority_count": highest if leader else 0,
        "majority_is_local": leader == local_digest if leader else False,
        "proof_of_creator_identity": False,
        "authorizes_role_change": False,
        "warning": (
            "Голоса основаны на общем секрете кластера и node_id; "
            "копии узлов и компрометация общего секрета могут создать ложное большинство."
        ),
    }


class CreatorConsensusService:
    """Сверка подписанных LAN-объявлений и ремонт только собственного кэша."""

    def __init__(self, storage: Any, discovery: Any):
        self.storage = storage
        self.discovery = discovery
        self._lock = threading.Lock()
        self._checked_at = 0.0
        self._last_alert = ""
        self._cache_state = "unchecked"

    def reconcile_informational_cache(self) -> str:
        """Исправить только свою производную запись, никогда не трогая роли.

        LeshyBrain здесь является кэшем. Ключ owner/Telegram, личные
        воспоминания и чужие записи Knowledge не изменяются.
        """
        digest = creator_identity_digest()
        desired = {
            "schema": 1,
            "source": "embedded_code",
            "name": CREATOR_FULL_NAME,
            "digest": digest,
        }
        expected = json.dumps(desired, ensure_ascii=False, sort_keys=True)
        with self.storage.connection() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (_CACHE_KEY,)).fetchone()
            if row is not None and str(row["value"]) == expected:
                self._cache_state = "consistent"
                return self._cache_state
            conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (_CACHE_KEY, expected),
            )
        self._cache_state = "repaired" if row is not None else "initialized"
        self.storage.audit("creator.identity.informational_cache", {
            "action": self._cache_state, "digest": digest,
            "checked_at": utcnow(),
        })
        return self._cache_state

    def report(self) -> dict[str, Any]:
        digest = creator_identity_digest()
        reader = getattr(self.discovery, "creator_peer_votes", None)
        peers = reader() if callable(reader) else {}
        summary = summarize_creator_votes(digest, peers)
        summary["informational_cache"] = self._cache_state
        return summary

    def tick(self) -> None:
        """Работает в существующем фоновом цикле, без новых демонов."""
        now = time.monotonic()
        with self._lock:
            # Кэш перепроверяется раз в 5 минут, но расхождения узлов
            # оцениваются при каждом обновлении существующего LAN-цикла.
            if not self._checked_at or now - self._checked_at >= _MIN_INTERVAL:
                self._checked_at = now
                self.reconcile_informational_cache()
            report = self.report()
            # Уменьшаем шум: сохраняем только изменение набора расхождений.
            signature = json.dumps(
                [report["status"], report["differing_peer_ids"],
                 report["majority_digest"]], sort_keys=True,
            )
            if report["differing_peer_ids"] and signature != self._last_alert:
                self.storage.audit("creator.identity.federation_divergence", {
                    "status": report["status"],
                    "nodes": report["differing_peer_ids"],
                    "local_digest": report["local_digest"],
                    "majority_digest": report["majority_digest"],
                    "checked_at": utcnow(),
                })
            self._last_alert = signature


__all__ = [
    "creator_identity_digest", "summarize_creator_votes", "CreatorConsensusService",
]
