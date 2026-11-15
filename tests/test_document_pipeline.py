from __future__ import annotations

from pathlib import Path

from leshy.document_pipeline import ingest_document, result_metadata


class FakeKnowledge:
    def __init__(self) -> None:
        self.candidates: list[dict] = []
        self.verified: list[str] = []

    def add_candidate(self, **kwargs) -> None:
        self.candidates.append(kwargs)

    def verify_topic(self, topic: str) -> None:
        self.verified.append(topic)


def test_document_pipeline_preserves_provenance_and_requests_verification(tmp_path: Path) -> None:
    path = tmp_path / "facts.txt"
    path.write_text(
        "Это достаточно длинное проверяемое утверждение о локальном документе и его происхождении. "
        "Второе достаточно длинное утверждение должно стать отдельным кандидатом для последующей проверки.",
        encoding="utf-8",
    )
    knowledge = FakeKnowledge()

    result = ingest_document(path, knowledge, title="Проверяемый документ")

    assert result.candidate_claims == 2
    assert result.verification_requested is True
    assert knowledge.verified == ["Проверяемый документ"]
    assert len(knowledge.candidates) == 2
    for candidate in knowledge.candidates:
        assert candidate["source_type"] == "material-document"
        assert candidate["source_url"] == path.resolve().as_uri()
        assert candidate["metadata_json"]["source_sha256"] == result.source_sha256
        assert candidate["metadata_json"]["source_bytes"] == path.stat().st_size
        assert candidate["metadata_json"]["document_adapter"] == "text"

    metadata = result_metadata(result)
    assert metadata["local"] is True
    assert metadata["source_sha256"] == result.source_sha256


def test_document_pipeline_does_not_verify_without_claims(tmp_path: Path) -> None:
    path = tmp_path / "short.txt"
    path.write_text("Слишком коротко.", encoding="utf-8")
    knowledge = FakeKnowledge()

    result = ingest_document(path, knowledge)

    assert result.candidate_claims == 0
    assert result.verification_requested is False
    assert knowledge.candidates == []
    assert knowledge.verified == []
