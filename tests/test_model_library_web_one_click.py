"""Static checks for the recommended model's no-manual-file download UX.

Not a browser or download E2E test.
"""
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "web" / "model-library-panel.js"


def test_recommended_model_has_one_click_download():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "id=\"modelLibraryRecommendedDownload\"" in source
    assert "downloadModel('qwen35-9b-original-q4km',false)" in source
    assert "/v1/model-library/download" in source
    assert "Скачать в Леший" in source


def test_direct_link_and_activation_remain_separate():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "Прямая ссылка на GGUF" in source
    assert "Сделать активной" in source
    assert "/v1/models/select" in source
    assert "model.url+(model.url.includes('?')?'&':'?')+'download=true'" in source
