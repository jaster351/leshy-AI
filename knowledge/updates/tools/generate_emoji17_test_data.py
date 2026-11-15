#!/usr/bin/env python3
"""Generate deterministic knowledge records from Unicode Emoji 17.0 emoji-test.txt."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

SOURCE_SHA256 = "1d8a944f88d7952f7ef7c5167fef3c67995bcae24543949710231b03a201acda"
SOURCE_URL = "https://www.unicode.org/Public/17.0.0/emoji/emoji-test.txt"
UTS_URL = "https://www.unicode.org/reports/tr51/tr51-29.html"
LICENSE_URL = "https://www.unicode.org/license.txt"
PUBLISHED_AT = "2025-08-04T20:55:31Z"
MANIFEST = "knowledge/updates/datasets/emoji17-emoji-test.manifest.json"
MAX_BYTES = 1_850_000
EXPECTED_STATUS_COUNTS = {
    "component": 9,
    "fully-qualified": 3944,
    "minimally-qualified": 1029,
    "unqualified": 243,
}


def source(checked_at: str) -> dict:
    return {
        "url": SOURCE_URL,
        "title": "Unicode Emoji 17.0 — emoji-test.txt",
        "source_family": "unicode-emoji-17.0-test-data",
        "source_version": "Emoji 17.0, 2025-08-04",
        "published_at": PUBLISHED_AT,
        "checked_at": checked_at,
        "license": "Unicode-3.0",
        "license_url": LICENSE_URL,
        "source_checksum": {"algorithm": "sha256", "value": SOURCE_SHA256},
    }


def specification(checked_at: str) -> dict:
    return {
        "url": UTS_URL,
        "title": "UTS #51 revision 29 — Unicode Emoji 17.0",
        "source_family": "unicode-uts51-17.0",
        "source_version": "Revision 29, Emoji 17.0",
        "published_at": "2025-09-04",
        "checked_at": checked_at,
        "license": "Unicode-3.0",
        "license_url": LICENSE_URL,
    }


def parse(raw: bytes, checked_at: str) -> list[dict]:
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("source SHA-256 mismatch")
    rows = []
    group = subgroup = None
    status_counts: dict[str, int] = {}
    for line_no, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if line.startswith("# group:"):
            group = line.split(":", 1)[1].strip()
            continue
        if line.startswith("# subgroup:"):
            subgroup = line.split(":", 1)[1].strip()
            continue
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(
            r"([0-9A-F ]+?)\s*;\s*(component|fully-qualified|minimally-qualified|unqualified)\s*#\s*(\S+)\s+E([0-9.]+)\s+(.+)",
            line,
        )
        if not match:
            raise ValueError(f"unparsed source line {line_no}: {line!r}")
        code_points_raw, status, sample, emoji_version, english_name = match.groups()
        code_points = code_points_raw.split()
        sequence = " ".join(f"U+{cp}" for cp in code_points)
        dataset_key = status + ":" + "-".join(code_points)
        identifier = "emoji17-" + status.replace("-", "_") + "-" + "-".join(cp.lower() for cp in code_points)
        claim = (
            f"В тестовых данных Unicode Emoji 17.0 последовательность {sequence} имеет статус "
            f"{status}; в источнике для неё указаны версия Emoji E{emoji_version} и английское краткое имя "
            f"{json.dumps(english_name, ensure_ascii=False)}."
        )
        rows.append({
            "schema_version": 1,
            "id": identifier,
            "version": 1,
            "language": "ru",
            "topic": f"Последовательность Emoji 17.0 {sequence}",
            "claim": claim,
            "category": "culture",
            "data_class": "static",
            "checked_at": checked_at,
            "ttl_seconds": None,
            "review_status": "verified",
            "sources": [source(checked_at), specification(checked_at)],
            "event_date": None,
            "published_at": PUBLISHED_AT,
            "source_version": "Unicode Emoji 17.0",
            "limitations": "Статус и имя относятся к emoji-test.txt версии 17.0. Глиф зависит от платформы; английское имя из тестового файла не является переводом, определением объекта или гарантией одинакового изображения.",
            "curation_method": "deterministic_structured_source_transform",
            "dataset": "unicode-emoji-17.0-emoji-test",
            "dataset_section": "emoji-test.txt",
            "dataset_key": dataset_key,
            "dataset_license": "Unicode-3.0",
            "dataset_manifest": MANIFEST,
            "source_line": line_no,
            "emoji_test": {
                "code_points": code_points,
                "status": status,
                "sample": sample,
                "emoji_version": emoji_version,
                "english_name": english_name,
                "group": group,
                "subgroup": subgroup,
            },
        })
        status_counts[status] = status_counts.get(status, 0) + 1
    if len(rows) != 5225 or status_counts != EXPECTED_STATUS_COUNTS:
        raise ValueError(f"unexpected source counts: rows={len(rows)}, statuses={status_counts}")
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("duplicate generated id")
    if len({row["dataset_key"] for row in rows}) != len(rows):
        raise ValueError("duplicate generated dataset key")
    return rows


def write_chunks(rows: list[dict], output_root: Path, output_prefix: str) -> None:
    chunks: list[list[bytes]] = []
    current: list[bytes] = []
    current_size = 0
    for row in rows:
        encoded = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if current and current_size + len(encoded) > MAX_BYTES:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(encoded)
        current_size += len(encoded)
    if current:
        chunks.append(current)
    for number, chunk in enumerate(chunks, 1):
        output = output_root / f"{output_prefix}{number:02d}.jsonl"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"".join(chunk))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--checked-at", required=True)
    parser.add_argument("--output-prefix", required=True)
    args = parser.parse_args()
    write_chunks(parse(args.source.read_bytes(), args.checked_at), args.output_root, args.output_prefix)


if __name__ == "__main__":
    main()
