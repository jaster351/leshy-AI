"""New model entry is opt-in and must preserve old local fallback.

These tests are committed as regression fixtures; no build or test execution
is authorized by their inclusion.
"""
from leshy.model_library import RECOMMENDED_MODELS


def test_original_qwen35_9b_has_pinned_source():
    models = {row.id: row for row in RECOMMENDED_MODELS}
    model = models["qwen35-9b-original-q4km"]
    assert model.family == "Qwen3.5 9B"
    assert model.quant == "Q4_K_M"
    assert model.runtime == "modern"
    assert model.url.startswith("https://huggingface.co/bartowski/Qwen_Qwen3.5-9B-GGUF/resolve/c512afe1bc55a45304dbe577088139fb9405b02e/")
    assert model.url.endswith("/" + model.filename)


def test_model_catalog_still_keeps_legacy_fallback():
    models = {row.id: row for row in RECOMMENDED_MODELS}
    assert "qwen3-8b-q4km" in models
    assert "qwen35-9b-original-q4km" in models
    assert len(models) == len(set(models))
    assert len([row.filename for row in models.values()]) == len(set(row.filename for row in models.values()))
