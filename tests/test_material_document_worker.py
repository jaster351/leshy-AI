from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from leshy.document_learning import DocumentAdapterUnavailable
from leshy.document_pipeline import DocumentIngestResult
from leshy.material_document_worker import finish_material_document, process_material_document


class MaterialDocumentWorkerTests(unittest.TestCase):
    def test_verified_document_is_done_and_keeps_provenance(self):
        result = DocumentIngestResult(
            title="manual.pdf",
            source_uri="file:///manual.pdf",
            adapter="pdf",
            source_sha256="a" * 64,
            source_bytes=1234,
            pages_or_slides=3,
            characters=900,
            candidate_claims=4,
            verification_requested=True,
        )
        with patch("leshy.material_document_worker.ingest_document", return_value=result):
            outcome = process_material_document("manual.pdf", object())
        self.assertEqual(outcome.status, "done")
        self.assertEqual(outcome.metadata["source_sha256"], "a" * 64)
        self.assertEqual(outcome.metadata["adapter"], "pdf")
        self.assertEqual(outcome.metadata["candidate_claims"], 4)
        self.assertFalse(outcome.metadata["vision_pending"])

    def test_empty_or_scanned_pipeline_result_fails_closed(self):
        result = DocumentIngestResult(
            title="scan.pdf",
            source_uri="file:///scan.pdf",
            adapter="pdf",
            source_sha256="b" * 64,
            source_bytes=4321,
            pages_or_slides=2,
            characters=0,
            candidate_claims=0,
            verification_requested=False,
        )
        with patch("leshy.material_document_worker.ingest_document", return_value=result):
            outcome = process_material_document("scan.pdf", object())
        self.assertEqual(outcome.status, "blocked")
        self.assertTrue(outcome.metadata["ocr_or_vision_pending"])
        self.assertTrue(outcome.metadata["adapter_pending"])

    def test_real_extraction_ocr_signal_is_blocked_not_failed(self):
        with patch("leshy.material_document_worker.ingest_document", side_effect=ValueError("В документе не найден извлекаемый текст; возможно, нужен OCR")):
            outcome = process_material_document("scan.pdf", object(), title="Скан")
        self.assertEqual(outcome.status, "blocked")
        self.assertEqual(outcome.title, "Скан")
        self.assertTrue(outcome.metadata["ocr_or_vision_pending"])
        self.assertIn("OCR/vision", outcome.summary)

    def test_missing_optional_adapter_is_blocked_not_failed(self):
        with patch("leshy.material_document_worker.ingest_document", side_effect=DocumentAdapterUnavailable("Для PDF установите pypdf")):
            outcome = process_material_document("manual.pdf", object())
        self.assertEqual(outcome.status, "blocked")
        self.assertTrue(outcome.metadata["adapter_pending"])
        self.assertFalse(outcome.metadata["ocr_or_vision_pending"])
        self.assertIn("pypdf", outcome.summary)

    def test_unexpected_value_error_is_not_hidden(self):
        with patch("leshy.material_document_worker.ingest_document", side_effect=ValueError("corrupt internal state")):
            with self.assertRaisesRegex(ValueError, "corrupt internal state"):
                process_material_document("manual.pdf", object())

    def test_finish_bridge_persists_verified_outcome_with_provenance(self):
        coordinator = Mock()
        result = DocumentIngestResult(
            title="guide.docx",
            source_uri="file:///guide.docx",
            adapter="docx",
            source_sha256="c" * 64,
            source_bytes=2048,
            pages_or_slides=1,
            characters=700,
            candidate_claims=3,
            verification_requested=True,
        )
        row = {"id": 42, "source": "guide.docx", "title": "Руководство"}
        with patch("leshy.material_document_worker.ingest_document", return_value=result):
            outcome = finish_material_document(coordinator, row, object())
        self.assertEqual(outcome.status, "done")
        coordinator.finish_material.assert_called_once()
        args, kwargs = coordinator.finish_material.call_args
        self.assertEqual(args, (42,))
        self.assertEqual(kwargs["status"], "done")
        self.assertEqual(kwargs["metadata"]["source_sha256"], "c" * 64)
        self.assertEqual(kwargs["metadata"]["adapter"], "docx")

    def test_finish_bridge_persists_blocked_ocr_without_marking_done(self):
        coordinator = Mock()
        row = {"id": 7, "source": "scan.pdf", "title": "Скан"}
        with patch("leshy.material_document_worker.ingest_document", side_effect=ValueError("нужен OCR: не найден извлекаемый текст")):
            outcome = finish_material_document(coordinator, row, object())
        self.assertEqual(outcome.status, "blocked")
        _, kwargs = coordinator.finish_material.call_args
        self.assertEqual(kwargs["status"], "blocked")
        self.assertTrue(kwargs["metadata"]["ocr_or_vision_pending"])


if __name__ == "__main__":
    unittest.main()
