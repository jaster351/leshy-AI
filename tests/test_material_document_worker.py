from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from leshy.document_pipeline import DocumentIngestResult
from leshy.material_document_worker import process_material_document


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

    def test_empty_or_scanned_document_fails_closed(self):
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
        self.assertIn("OCR/vision", outcome.summary)


if __name__ == "__main__":
    unittest.main()
