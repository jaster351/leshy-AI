from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Protocol

from .logging_paths import central_log_path
from .modules import ModuleManifest, ModuleRequest, ModuleResponse


class NotificationSink(Protocol):
    """Канал доставки коротких пользовательских уведомлений."""

    def send(self, text: str) -> None: ...


@dataclass(frozen=True, slots=True)
class LearningEvent:
    """Одно подтверждённое событие обучения Лешего."""

    topic: str
    summary: str
    importance: str = "normal"
    sources_checked: int = 0
    accepted_facts: int = 0
    pending_facts: int = 0
    details: str = ""
    created_at: str = ""

    def normalized(self) -> "LearningEvent":
        importance = self.importance.strip().lower()
        if importance not in {"important", "normal", "detail"}:
            raise ValueError("Уровень события обучения должен быть important, normal или detail")
        topic = self.topic.strip()
        summary = self.summary.strip()
        if not topic:
            raise ValueError("Тема события обучения не может быть пустой")
        if not summary:
            raise ValueError("Описание события обучения не может быть пустым")
        created_at = self.created_at.strip() or datetime.now(timezone.utc).isoformat()
        return LearningEvent(
            topic=topic,
            summary=summary,
            importance=importance,
            sources_checked=max(0, int(self.sources_checked)),
            accepted_facts=max(0, int(self.accepted_facts)),
            pending_facts=max(0, int(self.pending_facts)),
            details=self.details.strip(),
            created_at=created_at,
        )


class LearningNotificationCenter:
    """Единая точка журналирования обучения и подготовки уведомлений владельцу."""

    def __init__(self, brain_dir: Path, sink: NotificationSink | None = None) -> None:
        self.brain_dir = Path(brain_dir)
        self.sink = sink
        self.events_path = central_log_path(self.brain_dir, "learning-events.jsonl")
        self.digest_path = central_log_path(self.brain_dir, "learning-digest-pending.jsonl")

    @staticmethod
    def _append_jsonl(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict]:
        if not path.exists():
            return []
        result: list[dict] = []
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                result.append(value)
        return result

    @staticmethod
    def _render_event(event: LearningEvent) -> str:
        parts = [f"Изучил тему «{event.topic}»: {event.summary}"]
        counters: list[str] = []
        if event.sources_checked:
            counters.append(f"источников проверено: {event.sources_checked}")
        if event.accepted_facts:
            counters.append(f"подтверждено фактов: {event.accepted_facts}")
        if event.pending_facts:
            counters.append(f"на дополнительной проверке: {event.pending_facts}")
        if counters:
            parts.append("; ".join(counters))
        return ". ".join(parts)

    def record(self, event: LearningEvent) -> LearningEvent:
        """Записать событие в общий журнал и при необходимости подготовить уведомление."""
        normalized = event.normalized()
        payload = asdict(normalized)
        self._append_jsonl(self.events_path, payload)

        if normalized.importance == "important":
            if self.sink is not None:
                self.sink.send(self._render_event(normalized))
        elif normalized.importance == "normal":
            self._append_jsonl(self.digest_path, payload)

        return normalized

    def flush_digest(self, *, max_items: int = 20) -> str:
        """Собрать накопленные обычные события в одну короткую сводку и очистить очередь."""
        pending = self._read_jsonl(self.digest_path)
        if not pending:
            return ""

        selected = pending[: max(1, int(max_items))]
        lines = ["Сводка обучения Лешего:"]
        for item in selected:
            topic = str(item.get("topic", "без темы"))
            summary = str(item.get("summary", "")).strip()
            lines.append(f"— {topic}: {summary}")
        remaining = pending[len(selected) :]
        if remaining:
            lines.append(f"— ещё событий в очереди: {len(remaining)}")

        text = "\n".join(lines)
        self.digest_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in remaining),
            encoding="utf-8",
        )
        if self.sink is not None:
            self.sink.send(text)
        return text

    def recent_summary(self, *, limit: int = 10) -> str:
        """Вернуть последние события обучения для ответа на вопрос владельца."""
        events = self._read_jsonl(self.events_path)
        if not events:
            return "Пока в журнале нет завершённых событий обучения."
        selected = events[-max(1, int(limit)) :]
        lines = ["Последние результаты обучения:"]
        for item in reversed(selected):
            topic = str(item.get("topic", "без темы"))
            summary = str(item.get("summary", "")).strip()
            lines.append(f"— {topic}: {summary}")
        return "\n".join(lines)


class LearningNotificationModule:
    """Подключаемый модуль для запросов о прогрессе обучения."""

    manifest = ModuleManifest(
        name="learning-notifications",
        version="0.1.0",
        description="Отчёты владельцу о том, чему Леший научился",
        priority=80,
    )

    _TRIGGERS = (
        "чему ты научился",
        "что ты изучил",
        "что изучил сегодня",
        "что ты сейчас изучаешь",
        "покажи прогресс обучения",
        "отчёт об обучении",
        "отчет об обучении",
    )

    def __init__(self, center: LearningNotificationCenter) -> None:
        self.center = center

    def can_handle(self, request: ModuleRequest) -> bool:
        text = request.text.strip().lower()
        return any(trigger in text for trigger in self._TRIGGERS)

    def handle(self, request: ModuleRequest) -> ModuleResponse:
        return ModuleResponse(
            handled=True,
            text=self.center.recent_summary(),
            metadata={"module": self.manifest.name},
        )
