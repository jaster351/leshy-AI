from pathlib import Path
import tempfile
import unittest

from leshy.document_learning import DocumentAdapterUnavailable, extract_document, supported_document_suffixes


class DocumentLearningTests(unittest.TestCase):
    def test_plain_text_is_extracted_locally(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "knowledge.md"
            path.write_text("Леший изучает локальный документ.", encoding="utf-8")
            result = extract_document(path)
            self.assertEqual(result.adapter, "text")
            self.assertIn("локальный документ", result.text)

    def test_supported_formats_are_explicit(self):
        self.assertEqual(supported_document_suffixes(), (".docx", ".md", ".pdf", ".pptx", ".txt"))

    def test_unknown_format_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unknown.bin"
            path.write_bytes(b"not a document")
            with self.assertRaises(DocumentAdapterUnavailable):
                extract_document(path)

    def test_missing_file_is_not_silently_accepted(self):
        with self.assertRaises(FileNotFoundError):
            extract_document("definitely-missing-leshy-document.pdf")


if __name__ == "__main__":
    unittest.main()
