from __future__ import annotations

"""Локальное извлечение текста из документов для контура human learning.

Модуль не использует облачные API. Опциональные зависимости загружаются лениво,
поэтому отсутствие поддержки конкретного формата не ломает основной runtime.
"""

from dataclasses import dataclass, replace
import hashlib
from pathlib import Path
from typing import Callable


MAX_DOCUMENT_CHARS = 2_000_000


class DocumentAdapterUnavailable(RuntimeError):
    """Формат известен, но его локальная опциональная зависимость не установлена."""


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    text: str
    adapter: str
    pages_or_slides: int = 0
    source_sha256: str = ""
    source_bytes: int = 0


def _clean(parts: list[str]) -> str:
    text = "\n\n".join(part.strip() for part in parts if part and part.strip())
    return text[:MAX_DOCUMENT_CHARS]


def _extract_text(path: Path) -> ExtractedDocument:
    return ExtractedDocument(path.read_text(encoding="utf-8", errors="replace")[:MAX_DOCUMENT_CHARS], "text")


def _extract_pdf(path: Path) -> ExtractedDocument:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DocumentAdapterUnavailable("Для PDF установите опциональную зависимость pypdf") from exc
    reader = PdfReader(str(path))
    parts = [(page.extract_text() or "") for page in reader.pages]
    return ExtractedDocument(_clean(parts), "pypdf", len(reader.pages))


def _extract_docx(path: Path) -> ExtractedDocument:
    try:
        from docx import Document
    except ImportError as exc:
        raise DocumentAdapterUnavailable("Для DOCX установите опциональную зависимость python-docx") from exc
    doc = Document(str(path))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return ExtractedDocument(_clean(parts), "python-docx")


def _extract_pptx(path: Path) -> ExtractedDocument:
    try:
        from pptx import Presentation
    except ImportError as exc:
        raise DocumentAdapterUnavailable("Для PPTX установите опциональную зависимость python-pptx") from exc
    presentation = Presentation(str(path))
    parts: list[str] = []
    for slide in presentation.slides:
        for shape in slide.shapes:
            text = getattr(shape, "text", "")
            if text:
                parts.append(str(text))
    return ExtractedDocument(_clean(parts), "python-pptx", len(presentation.slides))


_ADAPTERS: dict[str, Callable[[Path], ExtractedDocument]] = {
    ".txt": _extract_text,
    ".md": _extract_text,
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
    ".pptx": _extract_pptx,
}


def supported_document_suffixes() -> tuple[str, ...]:
    return tuple(sorted(_ADAPTERS))


def _fingerprint(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def extract_document(path: str | Path) -> ExtractedDocument:
    source = Path(path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(source)
    suffix = source.suffix.lower()
    adapter = _ADAPTERS.get(suffix)
    if adapter is None:
        raise DocumentAdapterUnavailable(f"Локальный адаптер для {suffix or 'файла без расширения'} пока не реализован")
    result = adapter(source)
    if not result.text.strip():
        raise ValueError("В документе не найден извлекаемый текст; возможно, нужен OCR")
    source_sha256, source_bytes = _fingerprint(source)
    return replace(result, source_sha256=source_sha256, source_bytes=source_bytes)


__all__ = ["DocumentAdapterUnavailable", "ExtractedDocument", "extract_document", "supported_document_suffixes"]
