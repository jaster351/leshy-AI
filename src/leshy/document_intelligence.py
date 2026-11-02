from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .diagnostics import event, problem


@dataclass(frozen=True, slots=True)
class DocumentResult:
    path: str
    markdown: str
    engine: str
    size_bytes: int
    metadata: dict[str, Any]


class DocumentIntelligence:
    """Локальное преобразование документов в LLM/RAG-friendly Markdown.

    Сетевые URL намеренно не принимаются. Файл читается только локально с правами
    процесса Лешего. Основной движок — Microsoft MarkItDown; PDF имеет встроенный
    резерв через pypdf, чтобы базовый разбор не зависел от одного конвертера.
    """

    def __init__(self, *, max_bytes: int = 128 * 1024 * 1024):
        self.max_bytes = max(1 * 1024 * 1024, min(int(max_bytes), 1024 * 1024 * 1024))

    @staticmethod
    def _validate(path: str | Path) -> Path:
        raw = str(path).strip()
        if raw.lower().startswith(("http://", "https://", "ftp://")):
            raise ValueError("Document Intelligence принимает только локальные файлы")
        candidate = Path(raw).expanduser().resolve()
        if not candidate.is_file():
            raise FileNotFoundError(str(candidate))
        return candidate

    @staticmethod
    def _fallback_pdf(path: Path) -> str:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        parts: list[str] = []
        for index, page in enumerate(reader.pages, start=1):
            text = str(page.extract_text() or "").strip()
            parts.append(f"\n\n## Страница {index}\n\n{text}")
        return "".join(parts).strip()

    @staticmethod
    def _fallback_text(path: Path) -> str:
        if path.suffix.lower() not in {".txt", ".md", ".markdown", ".csv", ".json", ".log"}:
            raise RuntimeError("Для этого формата требуется MarkItDown")
        return path.read_text(encoding="utf-8", errors="replace")

    def convert(self, path: str | Path) -> DocumentResult:
        file_path = self._validate(path)
        size = file_path.stat().st_size
        if size > self.max_bytes:
            raise ValueError(f"Документ слишком большой: {size} байт, лимит {self.max_bytes}")

        event("document_intelligence_started", path=str(file_path), size_bytes=size)
        try:
            from markitdown import MarkItDown

            converter = MarkItDown(enable_plugins=False)
            convert_local = getattr(converter, "convert_local", None)
            result = convert_local(str(file_path)) if callable(convert_local) else converter.convert(str(file_path))
            markdown = str(
                getattr(result, "markdown", None)
                or getattr(result, "text_content", None)
                or ""
            ).strip()
            if not markdown:
                raise RuntimeError("MarkItDown вернул пустой результат")
            engine = "markitdown"
        except Exception as primary_exc:
            problem("document_intelligence_markitdown_failed", str(primary_exc), path=str(file_path))
            try:
                if file_path.suffix.lower() == ".pdf":
                    markdown = self._fallback_pdf(file_path)
                    engine = "pypdf"
                else:
                    markdown = self._fallback_text(file_path)
                    engine = "text"
            except Exception:
                raise RuntimeError(f"Не удалось разобрать документ: {primary_exc}") from primary_exc

        event(
            "document_intelligence_completed",
            path=str(file_path),
            engine=engine,
            chars=len(markdown),
        )
        return DocumentResult(
            path=str(file_path),
            markdown=markdown,
            engine=engine,
            size_bytes=size,
            metadata={"suffix": file_path.suffix.lower(), "name": file_path.name},
        )


__all__ = ["DocumentIntelligence", "DocumentResult"]
