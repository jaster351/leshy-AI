"""Static source-contract checks for the diagnostic controls (not browser E2E)."""

from pathlib import Path


WEB = Path(__file__).resolve().parents[1] / "web" / "chat-extras.js"


def test_copy_button_uses_entire_concise_diagnostic():
    source = WEB.read_text(encoding="utf-8")
    assert "Копировать результат" in source
    assert "copyText(intro,copyVerdict)" in source
    assert "copyText(verdict,copyVerdict)" not in source


def test_full_diagnostic_can_be_downloaded_as_utf8_text():
    source = WEB.read_text(encoding="utf-8")
    assert "Скачать полный протокол (.txt)" in source
    assert "text/plain;charset=utf-8" in source
    assert "link.download=filename" in source
    assert "report+'\\n'" in source
