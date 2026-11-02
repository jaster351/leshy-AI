from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from leshy.document_intelligence import DocumentIntelligence


class _TextContentResult:
    text_content = "Актуальный API MarkItDown"


class _Converter:
    def convert_local(self, path):
        return _TextContentResult()


class DocumentIntelligenceCompatibilityTests(unittest.TestCase):
    def test_markitdown_text_content_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_text("fallback", encoding="utf-8")
            result = DocumentIntelligence(converter_factory=_Converter).convert(path)
        self.assertEqual(result.engine, "markitdown")
        self.assertEqual(result.markdown, "Актуальный API MarkItDown")

    def test_result_text_prefers_current_text_content_api(self) -> None:
        class Both:
            text_content = "current"
            markdown = "legacy"

        self.assertEqual(DocumentIntelligence._result_text(Both()), "current")


if __name__ == "__main__":
    unittest.main()
