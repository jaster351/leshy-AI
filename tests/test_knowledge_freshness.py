from datetime import datetime, timedelta, timezone

import pytest

from leshy.knowledge import KnowledgeBase, is_fresh
from leshy.storage import Storage


def test_storage_migrates_existing_brain_without_data_loss(tmp_path):
    storage = Storage(tmp_path / "brain.db")
    with storage.connection() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(knowledge)")}
    assert {"data_class", "checked_at", "ttl_seconds"} <= columns


def test_expired_dynamic_knowledge_is_never_returned_as_current(tmp_path):
    storage = Storage(tmp_path / "brain.db")
    kb = KnowledgeBase(storage, min_trusted_confidence=0.0)
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    kid = kb.add_candidate(topic="погода", claim="Сейчас +20", source_url="https://example.gov/weather", data_class="dynamic", checked_at=old, ttl_seconds=60)
    with storage.connection() as conn:
        conn.execute("UPDATE knowledge SET status='trusted' WHERE id=?", (kid,))
    assert kb.search("погода сейчас") == []


def test_fresh_dynamic_knowledge_keeps_provenance_and_check_time(tmp_path):
    storage = Storage(tmp_path / "brain.db")
    kb = KnowledgeBase(storage, min_trusted_confidence=0.0)
    checked = datetime.now(timezone.utc).isoformat()
    kid = kb.add_candidate(topic="курс", claim="USD = 90", source_url="https://example.gov/rates", data_class="dynamic", checked_at=checked, ttl_seconds=3600)
    with storage.connection() as conn:
        conn.execute("UPDATE knowledge SET status='trusted' WHERE id=?", (kid,))
    row = kb.search("курс USD")[0]
    assert row["source_url"] == "https://example.gov/rates"
    assert row["checked_at"] == checked
    assert row["ttl_seconds"] == 3600
    assert is_fresh(row)


def test_recheck_of_same_dynamic_claim_refreshes_checked_at_and_ttl(tmp_path):
    storage = Storage(tmp_path / "brain.db")
    kb = KnowledgeBase(storage, min_trusted_confidence=0.0)
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    first_id = kb.add_candidate(topic="погода", claim="Сейчас +20", source_url="https://example.gov/weather", data_class="dynamic", checked_at=old, ttl_seconds=60)
    second_id = kb.add_candidate(topic="погода", claim="Сейчас +20", source_url="https://example.gov/weather", data_class="dynamic", checked_at=fresh, ttl_seconds=600)
    assert second_id == first_id
    with storage.connection() as conn:
        row = dict(conn.execute("SELECT * FROM knowledge WHERE id=?", (first_id,)).fetchone())
        audit = conn.execute("SELECT event_type,payload_json FROM audit_log WHERE event_type='knowledge.dynamic_rechecked' ORDER BY id DESC LIMIT 1").fetchone()
    assert row["checked_at"] == fresh
    assert row["ttl_seconds"] == 600
    assert is_fresh(row)
    assert audit is not None
    assert audit["event_type"] == "knowledge.dynamic_rechecked"


def test_static_knowledge_does_not_expire_without_ttl(tmp_path):
    storage = Storage(tmp_path / "brain.db")
    kb = KnowledgeBase(storage, min_trusted_confidence=0.0)
    kid = kb.add_candidate(topic="python", claim="Python is a programming language", source_url="https://docs.python.org/")
    with storage.connection() as conn:
        conn.execute("UPDATE knowledge SET status='trusted' WHERE id=?", (kid,))
    assert kb.search("python programming")


def test_invalid_data_class_is_rejected(tmp_path):
    kb = KnowledgeBase(Storage(tmp_path / "brain.db"))
    with pytest.raises(ValueError):
        kb.add_candidate(topic="x", claim="y", source_url="https://example.com", data_class="temporary")
