from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_legacy_archive_toggle_is_hidden_but_canonical_archive_remains_visible():
    layout = (ROOT / "web" / "chat-layout-status.js").read_text(encoding="utf-8")
    sidebar = (ROOT / "web" / "sidebar-session-actions.js").read_text(encoding="utf-8")

    assert "#sessionArchiveToggle{display:none!important}" in layout
    assert "toggle.id='leshyArchiveToggle'" in sidebar
    assert "ICONS.archive+'<span>Архив</span>'" in sidebar
    assert "#leshyArchiveToggle{display:none" not in layout
