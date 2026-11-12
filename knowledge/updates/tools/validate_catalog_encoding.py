#!/usr/bin/env python3
"""Validate text/JSON integrity of the public knowledge folder before publishing.

This validator only reports defects; it never guesses or overwrites damaged text.
Usage: python knowledge/updates/tools/validate_catalog_encoding.py
       python knowledge/updates/tools/validate_catalog_encoding.py path/to/new.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
from leshy.text_integrity import corruption_reason  # noqa: E402

TEXT_EXTENSIONS = {".json", ".jsonl", ".md", ".py", ".csv"}
MAX_ERRORS = 100


def inspect_file(path: Path) -> tuple[int, list[str]]:
    errors: list[str] = []
    rows = 0
    try:
        data = path.read_text(encoding="utf-8-sig", errors="strict")
    except (UnicodeError, OSError) as exc:
        return 0, [f"{path}: not valid UTF-8: {exc}"]

    reason = corruption_reason(data)
    if reason:
        errors.append(f"{path}: damaged text: {reason}")

    if path.suffix == ".json":
        try:
            json.loads(data)
            rows += 1
        except (ValueError, TypeError) as exc:
            errors.append(f"{path}: invalid JSON: {exc}")
    elif path.suffix == ".jsonl":
        for line_number, line in enumerate(data.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except (ValueError, TypeError) as exc:
                errors.append(f"{path}:{line_number}: invalid JSONL: {exc}")
                if len(errors) >= MAX_ERRORS:
                    break
                continue
            rows += 1
            if not isinstance(record, dict):
                errors.append(f"{path}:{line_number}: expected JSON object")
    return rows, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Reject corrupt Unicode and invalid JSON in Leshy knowledge.")
    parser.add_argument("paths", nargs="*", type=Path, help="Files or folders; defaults to knowledge/")
    options = parser.parse_args()
    targets = options.paths or [REPOSITORY_ROOT / "knowledge"]
    scanned = 0
    rows = 0
    problems: list[str] = []
    for target in targets:
        if not target.exists():
            problems.append(f"{target}: missing")
            continue
        files = sorted(target.rglob("*")) if target.is_dir() else [target]
        for path in files:
            if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS:
                continue
            count, issues = inspect_file(path)
            scanned += 1
            rows += count
            problems.extend(issues)
            if len(problems) >= MAX_ERRORS:
                break
        if len(problems) >= MAX_ERRORS:
            break
    for issue in problems[:MAX_ERRORS]:
        print("ERROR:", issue, file=sys.stderr)
    print(f"Knowledge text audit: files={scanned}, JSON/JSONL records={rows}, issues={len(problems)}")
    if problems:
        print("Do not publish damaged knowledge. Retrieve the original source again.", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
