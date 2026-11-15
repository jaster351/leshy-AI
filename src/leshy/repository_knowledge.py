"""Import public repository facts as data; never execute them or train model weights."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .knowledge import KnowledgeBase
from .storage import Storage, utcnow
from .text_integrity import validate_knowledge_text

SCHEMA_VERSION = 1
META_PREFIX = "repository_knowledge.record."
MAX_FILE_BYTES = 2 * 1024 * 1024


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("checked_at must be an ISO timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("checked_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_record(value: object, *, now: datetime | None = None) -> dict:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported knowledge schema")
    record = dict(value)
    def check_text_tree(item: object, field: str = "record") -> None:
        if isinstance(item, str):
            validate_knowledge_text(item, field=field)
        elif isinstance(item, dict):
            for key, child in item.items():
                check_text_tree(child, f"{field}.{key}")
        elif isinstance(item, list):
            for index, child in enumerate(item):
                check_text_tree(child, f"{field}[{index}]")
    check_text_tree(record)
    if not isinstance(record.get("id"), str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,159}", record["id"]):
        raise ValueError("invalid record id")
    if type(record.get("version")) is not int or record["version"] < 1:
        raise ValueError("version must be a positive integer")
    for field in ("topic", "claim", "category"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise ValueError(f"{field} must be nonempty text")
    if len(record["topic"]) > 500 or len(record["claim"]) > 8000:
        raise ValueError("knowledge record is too large")
    if record.get("review_status") not in {"candidate", "verified", "retracted"}:
        raise ValueError("invalid review_status")
    checked = _timestamp(record.get("checked_at"))
    current = now or datetime.now(timezone.utc)
    if checked > current + timedelta(minutes=5):
        raise ValueError("checked_at is in the future")
    if record.get("data_class") not in {"static", "dynamic"}:
        raise ValueError("invalid data_class")
    if record["data_class"] == "dynamic":
        ttl = record.get("ttl_seconds")
        if type(ttl) is not int or not 1 <= ttl <= 604800:
            raise ValueError("dynamic records require ttl_seconds in 1..604800")
    sources = record.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 8:
        raise ValueError("one to eight sources are required")
    urls = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("invalid source")
        url = source.get("url")
        if not isinstance(url, str) or len(url) > 3000:
            raise ValueError("invalid source URL")
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not host or parsed.username or parsed.password:
            raise ValueError("sources must use public HTTPS URLs without credentials")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host:
            raise ValueError("local source URLs are not allowed")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if not address.is_global:
                raise ValueError("private source URLs are not allowed")
        if url in urls:
            raise ValueError("duplicate source URL")
        urls.add(url)
        for field in ("title", "source_family"):
            if not isinstance(source.get(field), str) or not source[field].strip():
                raise ValueError(f"source {field} is required")
    return record


def _digest(record: dict) -> str:
    return sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _state(storage: Storage, record_id: str) -> dict:
    with storage.connection() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (META_PREFIX + record_id,)).fetchone()
    return json.loads(row["value"]) if row else {}


def _retire_owned(conn, record_id: str, ids: list, *, keep: set[int] | None = None, status: str = "stale") -> int:
    changed = 0
    for knowledge_id in ids:
        if keep and knowledge_id in keep:
            continue
        row = conn.execute("SELECT metadata_json FROM knowledge WHERE id=?", (knowledge_id,)).fetchone()
        if not row:
            continue
        try:
            origin = json.loads(row["metadata_json"]).get("repository_knowledge", {})
        except (ValueError, AttributeError, TypeError):
            continue
        # A pre-existing user/web record with the same claim is not owned by this importer.
        if origin.get("id") != record_id:
            continue
        conn.execute("UPDATE knowledge SET status=? WHERE id=?", (status, knowledge_id))
        changed += 1
    return changed


def import_record(knowledge: KnowledgeBase, record: dict, *, package_path: str) -> str:
    storage = knowledge.storage
    previous = _state(storage, record["id"])
    digest = _digest(record)
    if previous:
        if previous["version"] > record["version"]:
            return "skipped"
        if previous["version"] == record["version"]:
            if previous["digest"] != digest:
                raise ValueError("same id/version has different content; increment version")
            return "skipped"
    new_ids = []
    if record["review_status"] != "retracted":
        for source in record["sources"]:
            metadata = {
                "repository_knowledge": {
                    "id": record["id"], "version": record["version"], "package": package_path,
                    "digest": digest, "curator_review_status": record["review_status"],
                    "checked_at": record["checked_at"], "category": record["category"],
                    "sources": record["sources"], "limitations": record.get("limitations", ""),
                },
                "untrusted_material": True,
            }
            knowledge_id = knowledge.add_candidate(
                topic=record["topic"], claim=record["claim"], source_url=source["url"],
                source_title=source["title"], source_type="repository_knowledge",
                metadata_json=metadata, data_class=record["data_class"],
                checked_at=record["checked_at"] if record["data_class"] == "dynamic" else None,
                ttl_seconds=record.get("ttl_seconds") if record["data_class"] == "dynamic" else None,
            )
            with storage.connection() as conn:
                row = conn.execute("SELECT metadata_json FROM knowledge WHERE id=?", (knowledge_id,)).fetchone()
                try:
                    origin = json.loads(row["metadata_json"]).get("repository_knowledge", {})
                except (ValueError, AttributeError, TypeError):
                    origin = {}
                if origin.get("id") == record["id"]:
                    # A new revision invalidates the old local verdict, even when
                    # its wording is unchanged. Never overwrite an unrelated row.
                    conn.execute("UPDATE knowledge SET status='candidate',metadata_json=? WHERE id=?",
                                 (json.dumps(metadata, ensure_ascii=False, sort_keys=True), knowledge_id))
            new_ids.append(knowledge_id)
    state = {"version": record["version"], "digest": digest, "knowledge_ids": new_ids,
             "review_status": record["review_status"], "package": package_path, "imported_at": utcnow()}
    with storage.connection() as conn:
        _retire_owned(conn, record["id"], previous.get("knowledge_ids", []),
                      keep=set(new_ids), status="rejected" if record["review_status"] == "retracted" else "stale")
        conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (META_PREFIX + record["id"], json.dumps(state, ensure_ascii=False, sort_keys=True)))
        if new_ids and not conn.execute(
            "SELECT 1 FROM research_tasks WHERE query=? AND status IN ('queued','running') LIMIT 1",
            (record["topic"],),
        ).fetchone():
            conn.execute(
                "INSERT INTO research_tasks(query,reason,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                (record["topic"], "проверка публичного каталога knowledge/updates", "queued", utcnow(), utcnow()),
            )
    storage.audit("repository_knowledge.record_imported", {
        "id": record["id"], "version": record["version"], "review_status": record["review_status"],
        "knowledge_ids": new_ids, "package": package_path,
    })
    # The file's 'verified' label is curator metadata, not a local trusted verdict.
    # Existing verification/research remains responsible for promotion.
    return "retracted" if record["review_status"] == "retracted" else "imported"


def import_directory(knowledge: KnowledgeBase, directory: Path | str) -> dict:
    root = Path(directory).expanduser().resolve()
    result = {"directory": str(root), "imported": 0, "skipped": 0, "retracted": 0, "errors": []}
    if not root.is_dir():
        result["state"] = "directory_missing"
        return result
    for path in sorted(root.rglob("*.jsonl")):
        relative = str(path.relative_to(root))
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("package symlinks outside the catalog are not allowed")
            if path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("package exceeds the file size limit")
            # Validate the entire file and revision conflicts before importing any rows.
            records = [validate_record(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            seen = set()
            for record in records:
                if record["id"] in seen:
                    raise ValueError("duplicate record id in package")
                seen.add(record["id"])
                previous = _state(knowledge.storage, record["id"])
                if previous.get("version") == record["version"] and previous.get("digest") != _digest(record):
                    raise ValueError("same id/version has different content; increment version")
            for record in records:
                result[import_record(knowledge, record, package_path=relative)] += 1
        except (OSError, ValueError, TypeError, KeyError) as exc:
            result["errors"].append({"package": relative, "error": str(exc)[:500]})
    result["state"] = "partial" if result["errors"] else "complete"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Import public facts into LeshyBrain as candidates; model weights are unchanged.")
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args()
    result = import_directory(KnowledgeBase(Storage(args.database)), args.directory)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["errors"] or result["state"] == "directory_missing" else 0


if __name__ == "__main__":
    raise SystemExit(main())
