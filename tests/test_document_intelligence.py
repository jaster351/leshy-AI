from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from leshy.document_intelligence import DocumentIntelligence


class _TextContentResult:
    text_content = "Актуальный API MarkItDown"


class _Converter:
    def __init__(self, *args, **kwargs):
        pass

    def convert_local(self, path):
        return _TextContentResult()


class DocumentIntelligenceCompatibilityTests(unittest.TestCase):
    def test_markitdown_text_content_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_text("fallback", encoding="utf-8")
            with patch("markitdown.MarkItDown", _Converter):
                result = DocumentIntelligence().convert(path)
        self.assertEqual(result.engine, "markitdown")
        self.assertEqual(result.markdown, "Актуальный API MarkItDown")


if __name__ == "__main__":
    unittest.main()
