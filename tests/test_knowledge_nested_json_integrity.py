"""Regression cases for decoded JSON Unicode corruption in knowledge catalog."""
from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "knowledge/updates/tools/validate_catalog_encoding.py"
spec = importlib.util.spec_from_file_location("knowledge_encoding_audit", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_decoded_json_escaped_replacement_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"claim":"\\uFFFD"}\n', encoding="utf-8")
    rows, errors = module.inspect_file(path)
    assert rows == 1
    assert any("replacement_character" in error for error in errors)


def test_decoded_json_nested_surrogate_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "nested.json"
    path.write_text('{"sources":[{"title":"\\ud800"}]}', encoding="utf-8")
    rows, errors = module.inspect_file(path)
    assert rows == 1
    assert any("unpaired_surrogate" in error for error in errors)


def test_valid_nested_cyrillic_remains_accepted(tmp_path: Path) -> None:
    path = tmp_path / "good.jsonl"
    path.write_text('{"sources":[{"title":"Привет, мир"}]}\n', encoding="utf-8")
    assert module.inspect_file(path) == (1, [])
