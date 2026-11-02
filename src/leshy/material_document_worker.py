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

from .document_learning import DocumentAdapterUnavailable
from .document_pipeline import ingest_document, result_metadata


@dataclass(frozen=True, slots=True)
class MaterialDocumentOutcome:
    status: str
    title: str
    summary: str
    metadata: dict[str, Any]


def _blocked(path: str | Path, *, title: str, reason: str, ocr_pending: bool) -> MaterialDocumentOutcome:
    source = Path(path).expanduser()
    return MaterialDocumentOutcome(
        status="blocked",
        title=str(title or source.name),
        summary=reason,
        metadata={
            "adapter_pending": True,
            "ocr_or_vision_pending": bool(ocr_pending),
            "suffix": source.suffix.lower(),
            "local": True,
        },
    )


def process_material_document(path: str | Path, knowledge: Any, *, title: str = "") -> MaterialDocumentOutcome:
    """Извлечь документ и безопасно провести его через candidate -> verification.

    Ожидаемые ограничения локального document-слоя являются ``blocked``, а не
    ``failed``: отсутствие опционального адаптера и документ без извлекаемого
    текста не должны выглядеть как авария worker. Неожиданные ошибки намеренно
    не перехватываются и остаются видимыми диагностике.
    """
    try:
        result = ingest_document(path, knowledge, title=title)
    except DocumentAdapterUnavailable as exc:
        return _blocked(
            path,
            title=title,
            reason=f"Локальный адаптер документа пока недоступен: {exc}",
            ocr_pending=False,
        )
    except ValueError as exc:
        message = str(exc)
        if "OCR" not in message and "извлекаем" not in message.lower():
            raise
        return _blocked(
            path,
            title=title,
            reason="В документе не найден извлекаемый текст. Для скана или изображения требуется локальный OCR/vision.",
            ocr_pending=True,
        )

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
