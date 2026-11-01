from pathlib import Path
import hashlib
import tempfile
import unittest

from leshy.document_learning import DocumentAdapterUnavailable, extract_document, supported_document_suffixes


class DocumentLearningTests(unittest.TestCase):
    def test_plain_text_is_extracted_locally(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "knowledge.md"
            payload = "Леший изучает локальный документ."
            path.write_text(payload, encoding="utf-8")
            result = extract_document(path)
            self.assertEqual(result.adapter, "text")
            self.assertIn("локальный документ", result.text)
            raw = path.read_bytes()
            self.assertEqual(result.source_bytes, len(raw))
            self.assertEqual(result.source_sha256, hashlib.sha256(raw).hexdigest())

    def test_fingerprint_changes_when_source_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "knowledge.txt"
            path.write_text("Первая версия документа с достаточно полезным содержимым.", encoding="utf-8")
            first = extract_document(path)
            path.write_text("Вторая версия документа с изменённым содержимым.", encoding="utf-8")
            second = extract_document(path)
            self.assertNotEqual(first.source_sha256, second.source_sha256)

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
