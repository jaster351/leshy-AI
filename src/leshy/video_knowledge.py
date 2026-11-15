from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Protocol

from .knowledge import KnowledgeBase


@dataclass(frozen=True)
class VideoSegment:
    """Один временной фрагмент видеоматериала после мультимодального анализа."""

    start_seconds: float
    end_seconds: float
    transcript: str = ""
    ocr_text: str = ""
    visual_description: str = ""


@dataclass(frozen=True)
class VideoSource:
    """Описание исходного видеоматериала независимо от его платформы."""

    source_id: str
    source_url: str
    title: str
    source_kind: str = "video"


class VideoAnalyzer(Protocol):
    """Адаптер, который превращает видео в последовательность смысловых фрагментов."""

    def analyze(self, source: VideoSource) -> Iterable[VideoSegment]:
        ...


class VideoKnowledgeIngestor:
    """Переносит сведения из видео в общую базу знаний Лешего.

    Этот слой намеренно не зависит от YouTube, локальных файлов или конкретного
    движка распознавания. Адаптер источника отвечает за получение расшифровки,
    OCR и описания кадров, а данный класс отвечает только за безопасное добавление
    проверяемых утверждений в KnowledgeBase с сохранением таймкодов и происхождения.
    """

    def __init__(self, knowledge: KnowledgeBase, safety_gate=None):
        self.knowledge = knowledge
        self.safety_gate = safety_gate

    def ingest(
        self,
        *,
        topic: str,
        source: VideoSource,
        segments: Iterable[VideoSegment],
        verify: bool = True,
    ) -> dict[str, int]:
        topic = " ".join(str(topic).split()).strip()
        if not topic:
            raise ValueError("Тема видеоматериала не может быть пустой")

        accepted = 0
        quarantined = 0
        processed_segments = 0

        for segment in segments:
            processed_segments += 1
            if segment.end_seconds < segment.start_seconds:
                raise ValueError("Конец видеофрагмента не может быть раньше начала")

            modalities = (
                ("speech", segment.transcript),
                ("ocr", segment.ocr_text),
                ("vision", segment.visual_description),
            )
            for modality, raw_text in modalities:
                text = " ".join(str(raw_text).split()).strip()
                if not text:
                    continue

                safety = None
                if self.safety_gate is not None:
                    safety = self.safety_gate.classify(
                        text,
                        title=source.title,
                        url=source.source_url,
                    )
                    if not getattr(safety, "allow_learning", True):
                        quarantined += 1
                        continue

                for claim in self._extract_claims(text):
                    self.knowledge.add_candidate(
                        topic=topic,
                        claim=claim,
                        source_url=source.source_url,
                        source_title=source.title,
                        source_type=f"video-{modality}",
                        metadata_json={
                            "video_source_id": source.source_id,
                            "video_source_kind": source.source_kind,
                            "start_seconds": round(float(segment.start_seconds), 3),
                            "end_seconds": round(float(segment.end_seconds), 3),
                            "modality": modality,
                            "learning_safety": getattr(safety, "category", "unclassified"),
                            "learning_safety_confidence": getattr(safety, "confidence", 0.0),
                        },
                    )
                    accepted += 1

        verification = self.knowledge.verify_topic(topic) if verify and accepted else {
            "trusted": 0,
            "candidate": accepted,
            "conflict": 0,
        }
        return {
            "segments": processed_segments,
            "accepted_claims": accepted,
            "quarantined_fragments": quarantined,
            **verification,
        }

    @staticmethod
    def _extract_claims(text: str) -> list[str]:
        """Извлекает короткие проверяемые утверждения без пересказа всего видео."""

        out: list[str] = []
        seen: set[str] = set()
        for chunk in re.split(r"(?<=[.!?])\s+", text):
            chunk = " ".join(chunk.split()).strip()
            if 35 <= len(chunk) <= 500 and chunk not in seen:
                seen.add(chunk)
                out.append(chunk)
            if len(out) >= 20:
                break
        return out
