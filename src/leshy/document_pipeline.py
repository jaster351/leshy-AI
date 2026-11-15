from __future__ import annotations

"""Безопасный мост document extraction -> candidate -> verification.

Модуль отделён от фонового worker: его легко тестировать без потоков и сети.
Ни один документ не попадает напрямую в trusted knowledge — утверждения сначала
создаются как candidates, после чего запускается штатная верификация темы.
"""

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .document_learning import extract_document


@dataclass(frozen=True, slots=True)
class DocumentIngestResult:
    title: str
    source_uri: str
    adapter: str
    source_sha256: str
    source_bytes: int
    pages_or_slides: int
    characters: int
    candidate_claims: int
    verification_requested: bool


def _claims(text: str, *, limit: int = 80) -> list[str]:
    import re

    out: list[str] = []
    seen: set[str] = set()
    normalized = " ".join(str(text or "").split())
    for chunk in re.split(r"(?<=[.!?])\s+", normalized):
        claim = chunk.strip()
        if 35 <= len(claim) <= 500 and claim not in seen:
            seen.add(claim)
            out.append(claim)
        if len(out) >= limit:
            break
    return out


def ingest_document(path: str | Path, knowledge: Any, *, title: str = "") -> DocumentIngestResult:
    source = Path(path).expanduser().resolve()
    extracted = extract_document(source)
    topic = str(title or source.name).strip()[:500] or source.name
    source_uri = source.as_uri()
    claims = _claims(extracted.text)

    metadata = {
        "human_learning": True,
        "captured_via": "material-document",
        "document_adapter": extracted.adapter,
        "source_sha256": extracted.source_sha256,
        "source_bytes": extracted.source_bytes,
        "pages_or_slides": extracted.pages_or_slides,
    }
    for claim in claims:
        knowledge.add_candidate(
            topic=topic,
            claim=claim,
            source_url=source_uri,
            source_title=topic,
            source_type="material-document",
            metadata_json=metadata,
        )

    # Fail closed: пустой/слишком фрагментарный документ не запускает verification
    # и тем более не может создать видимость успешно усвоенных знаний.
    verification_requested = bool(claims)
    if verification_requested:
        knowledge.verify_topic(topic)

    return DocumentIngestResult(
        title=topic,
        source_uri=source_uri,
        adapter=extracted.adapter,
        source_sha256=extracted.source_sha256,
        source_bytes=extracted.source_bytes,
        pages_or_slides=extracted.pages_or_slides,
        characters=len(extracted.text),
        candidate_claims=len(claims),
        verification_requested=verification_requested,
    )


def result_metadata(result: DocumentIngestResult) -> dict[str, Any]:
    data = asdict(result)
    data.update({"local": True, "vision_pending": False, "asr_pending": False})
    return data


__all__ = ["DocumentIngestResult", "ingest_document", "result_metadata"]
