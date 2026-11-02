from __future__ import annotations

"""Интеграционный адаптер локальных документов для фонового обучения.

Модуль намеренно не зависит от потокового worker-кода: он превращает результат
проверяемого document pipeline в единый итог, который worker может атомарно
записать в learning_materials. Trusted knowledge по-прежнему создаётся только
штатной verification-логикой KnowledgeEngine.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .document_pipeline import ingest_document, result_metadata


@dataclass(frozen=True, slots=True)
class MaterialDocumentOutcome:
    status: str
    title: str
    summary: str
    metadata: dict[str, Any]


def process_material_document(path: str | Path, knowledge: Any, *, title: str = "") -> MaterialDocumentOutcome:
    """Извлечь документ и безопасно провести его через candidate -> verification.

    Документ без пригодных утверждений не помечается как успешно изученный:
    возвращается ``blocked``. Это важно для сканированных/пустых PDF и защищает
    UI от ложного статуса «готово» до появления OCR/vision слоя.
    """
    result = ingest_document(path, knowledge, title=title)
    metadata = result_metadata(result)

    if not result.verification_requested:
        metadata.update({"adapter_pending": True, "ocr_or_vision_pending": True})
        return MaterialDocumentOutcome(
            status="blocked",
            title=result.title,
            summary=(
                "Документ извлечён, но проверяемых текстовых утверждений не найдено. "
                "Для скана или изображения требуется локальный OCR/vision."
            ),
            metadata=metadata,
        )

    return MaterialDocumentOutcome(
        status="done",
        title=result.title,
        summary=(
            f"Локальный документ изучен через candidate → verification; "
            f"создано утверждений: {result.candidate_claims}."
        ),
        metadata=metadata,
    )


__all__ = ["MaterialDocumentOutcome", "process_material_document"]
