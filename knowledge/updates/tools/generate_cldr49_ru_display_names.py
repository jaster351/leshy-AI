#!/usr/bin/env python3
"""Reproduce CLDR 49 Russian language and territory display-name JSONL packages."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import xml.etree.ElementTree as ET

CHECKED_AT = "2026-11-11T18:03:19Z"
SOURCE_URL = "https://github.com/unicode-org/cldr/blob/release-49/common/main/ru.xml"
LICENSE_URL = "https://github.com/unicode-org/cldr/blob/release-49/LICENSE"
SOURCE_GIT_BLOB_SHA1 = "0307a700b08204fa8f0df18934b1c6f5ee30689c"
PUBLISHED_AT = "2026-10-21T22:13:30Z"
MANIFEST = "knowledge/updates/datasets/cldr49-ru-display-names.manifest.json"

def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def source():
    return {"url": SOURCE_URL, "title": "Unicode CLDR 49 — Russian locale data (common/main/ru.xml)",
            "source_family": "unicode-cldr-49-ru",
            "source_version": "CLDR 49, tag release-49, Git blob " + SOURCE_GIT_BLOB_SHA1,
            "published_at": PUBLISHED_AT, "checked_at": CHECKED_AT, "license": "Unicode-3.0",
            "license_url": LICENSE_URL,
            "source_checksum": {"algorithm": "git_blob_sha1", "value": SOURCE_GIT_BLOB_SHA1}}

def record(kind: str, code: str, name: str) -> dict:
    if kind == "language":
        category, label = "language", "языкового"
        limits = "Это локализованное отображаемое название из CLDR, а не определение языка, не утверждение о его статусе и не гарантия предпочтительного самоназвания."
    else:
        category, label = "geography", "территориального"
        limits = "CLDR включает страны, территории, макрорегионы и специальные коды; отображаемое название не устанавливает государственность, границы или политическое признание."
    section = "languages" if kind == "language" else "territories"\n    return {"schema_version": 1, "id": f"cldr49-ru-{kind}-{code.lower()}", "version": 1, "language": "ru",
            "topic": f"Русское отображаемое название {label} кода {code} в CLDR 49",
            "claim": f"В русской локали CLDR 49 для {label} кода «{code}» задано отображаемое название «{name}».",
            "category": category, "data_class": "static", "checked_at": CHECKED_AT, "ttl_seconds": None,
            "review_status": "verified", "sources": [source()], "event_date": None,
            "published_at": PUBLISHED_AT, "source_version": "CLDR 49, release-49", "limitations": limits,
            "curation_method": "deterministic_structured_source_transform",
            "dataset": "unicode-cldr-49-ru-display-names",
            "dataset_section": f"localeDisplayNames/{section}", "dataset_key": code,
            "dataset_license": "Unicode-3.0", "dataset_manifest": MANIFEST}

def select(root, plural: str, singular: str):
    parent = root.find("./localeDisplayNames/" + plural)
    if parent is None:
        raise ValueError("missing " + plural)
    rows = []
    for node in parent.findall(singular):
        if set(node.attrib) != {"type"} or not node.text or node.text.strip() == "↑↑↑":
            continue
        rows.append((node.attrib["type"], node.text.strip()))
    if len(rows) != len({code for code, _ in rows}):
        raise ValueError("duplicate source key in " + plural)
    return rows

def write(path: Path, rows, kind: str):
    content = "".join(json.dumps(record(kind, code, name), ensure_ascii=False, separators=(",", ":")) + "\n" for code, name in rows)
    if len(content.encode("utf-8")) >= 2 * 1024 * 1024:
        raise ValueError("output exceeds importer limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("source_xml", type=Path)
    p.add_argument("output_root", type=Path)
    args = p.parse_args()
    raw = args.source_xml.read_bytes()
    if git_blob_sha1(raw) != SOURCE_GIT_BLOB_SHA1:
        raise ValueError("source Git blob checksum mismatch")
    root = ET.fromstring(raw)
    languages = select(root, "languages", "language")
    territories = select(root, "territories", "territory")
    if (len(languages), len(territories)) != (574, 295):
        raise ValueError("source selection count mismatch")
    write(args.output_root / "knowledge/updates/2026-11-11/1800-cldr49-russian-language-display-names.jsonl", languages, "language")
    write(args.output_root / "knowledge/updates/2026-11-11/1800-cldr49-russian-territory-display-names.jsonl", territories, "territory")

if __name__ == "__main__":
    main()
