from __future__ import annotations

from dataclasses import asdict
import uuid
from typing import Any

from .config import Settings
from .hardware import detect_hardware
from .knowledge import KnowledgeBase
from .learning_control import LearningControl
from .llm import ChatMessage, EchoLLM, LLMUnavailable, OllamaLLM
from .model_download import ModelDownloader
from .native_chat import NativeGGUFLLM
from .research import DdgsSearch, FallbackSearch, ResearchEngine, SearxngSearch
from .self_learning import SelfLearningWorker
from .storage import Storage, utcnow
from .version import VERSION


class LeshyApp:
    """Рабочее ядро первой сборки: чат, история, знания, поиск и самообучение."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self.storage = Storage(self.settings.database_path)
        self.learning = LearningControl(self.storage, self.settings.idle_learning_enabled)
        self.knowledge = KnowledgeBase(self.storage, self.settings.min_trusted_confidence)
        self.research = ResearchEngine(
            self.storage,
            self.knowledge,
            FallbackSearch(SearxngSearch(self.settings.searxng_url, timeout=3), DdgsSearch()),
        )
        self.llm = self._build_llm()
        self.model_downloader = ModelDownloader(self.settings.native_models_dir / self.settings.native_chat_file)
        self.self_learning = SelfLearningWorker(self.storage, self.learning, self.research)
        self.telegram = None

    def _build_llm(self):
        backend = self.settings.llm_backend
        if backend == "echo":
            return EchoLLM()
        if backend == "ollama":
            return OllamaLLM(self.settings.ollama_url, self.settings.ollama_model)
        if backend != "native":
            raise ValueError(f"Неизвестный LLM backend: {backend}")
        return NativeGGUFLLM(
            self.settings.native_models_dir / self.settings.native_chat_file,
            n_ctx=self.settings.native_context_size,
        )

    def start_background(self) -> None:
        self.self_learning.start()

    def stop_background(self) -> None:
        self.self_learning.stop()

    def _ensure_session(self, owner_id: str, session_id: str | None) -> str:
        owner_id = (owner_id or "web:local").strip()[:200]
        session_id = (session_id or str(uuid.uuid4())).strip()[:200]
        now = utcnow()
        with self.storage.connection() as conn:
            row = conn.execute("SELECT owner_id FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row is not None and str(row["owner_id"]) != owner_id:
                raise PermissionError("Сессия принадлежит другому пользователю")
            if row is None:
                conn.execute(
                    "INSERT INTO sessions(id,owner_id,title,title_source,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (session_id, owner_id, "Новый диалог", "auto", now, now),
                )
        return session_id

    def history(self, owner_id: str, session_id: str, *, limit: int = 60) -> list[dict[str, Any]]:
        sid = self._ensure_session(owner_id, session_id)
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT role,content,created_at FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (sid, max(1, min(int(limit), 200))),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    @staticmethod
    def _needs_live_search(text: str) -> bool:
        value = text.lower()
        markers = (
            "/search ", "найди в интернете", "поищи в интернете", "проверь в интернете",
            "актуальн", "сегодня", "сейчас", "последние новости", "последняя версия",
            "текущая цена", "курс валют", "погода",
        )
        return any(marker in value for marker in markers)

    def live_search(self, query: str, *, limit: int = 5) -> dict:
        query = " ".join(str(query).replace("/search", "", 1).split()).strip()
        if not query:
            raise ValueError("Пустой поисковый запрос")
        return self.research.live_query(query, reason="прямой запрос пользователя", limit=limit)

    def queue_research(self, query: str) -> int:
        return self.self_learning.queue_topic(query)

    def chat(self, message: str, *, owner_id: str = "web:local", session_id: str | None = None) -> dict[str, Any]:
        message = str(message or "").strip()
        if not message:
            raise ValueError("Сообщение пустое")
        if len(message) > 32000:
            raise ValueError("Сообщение слишком длинное")
        sid = self._ensure_session(owner_id, session_id)
        now = utcnow()
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                (sid, "user", message, now),
            )
            rows = conn.execute(
                "SELECT role,content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 24",
                (sid,),
            ).fetchall()

        context: list[ChatMessage] = []
        trusted = self.knowledge.search(message, trusted_only=True, limit=5)
        if trusted:
            facts = "\n".join(f"- {item['claim']} (источник: {item['source_url']})" for item in trusted)
            context.append(ChatMessage("system", "Проверенные локальные знания Лешего:\n" + facts))

        sources: list[dict[str, str]] = []
        if self._needs_live_search(message):
            search_result = self.live_search(message, limit=5)
            sources = [
                {"title": str(item.get("title") or ""), "url": str(item.get("url") or "")}
                for item in search_result.get("results", [])
            ]
            if search_result.get("results"):
                snippets = "\n".join(
                    f"- {item.get('title','')}: {item.get('snippet','')} [{item.get('url','')}]"
                    for item in search_result["results"]
                )
                context.append(ChatMessage(
                    "system",
                    "Ниже результаты текущего веб-поиска. Они ещё candidate, не выдавай их как проверенные без оговорки:\n" + snippets,
                ))

        messages = context + [ChatMessage(str(row["role"]), str(row["content"])) for row in reversed(rows)]
        answer = self.llm.chat(messages)
        answered_at = utcnow()
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                (sid, "assistant", answer, answered_at),
            )
            conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (answered_at, sid))
            count = conn.execute("SELECT COUNT(*) AS c FROM messages WHERE session_id=?", (sid,)).fetchone()["c"]
            if int(count) <= 2:
                title = " ".join(message.split())[:80]
                conn.execute("UPDATE sessions SET title=?,updated_at=? WHERE id=?", (title, answered_at, sid))
        return {"session_id": sid, "answer": answer, "created_at": answered_at, "sources": sources}

    def set_learning(self, enabled: bool) -> bool:
        value = self.learning.set_enabled(bool(enabled), reason="Web/Telegram")
        self.self_learning.start()
        return value

    def status(self) -> dict[str, Any]:
        with self.storage.connection() as conn:
            stats = {
                "sessions": int(conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]),
                "messages": int(conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]),
                **self.knowledge.counts(),
            }
        try:
            hardware = asdict(detect_hardware())
        except Exception as exc:
            hardware = {"error": str(exc)}
        model_path = self.settings.native_models_dir / self.settings.native_chat_file
        telegram_status = self.telegram.status() if self.telegram is not None else {"configured": False, "running": False}
        return {
            "name": "Леший",
            "version": VERSION,
            "backend": self.settings.llm_backend,
            "model": getattr(self.llm, "model", self.settings.ollama_model),
            "model_ready": bool(model_path.is_file()) if self.settings.llm_backend == "native" else True,
            "model_download": self.model_downloader.status(),
            "brain_dir": str(self.settings.brain_dir),
            "learning_enabled": self.learning.is_enabled(),
            "learning_worker": self.self_learning.status(),
            "telegram": telegram_status,
            "stats": stats,
            "hardware": hardware,
        }


__all__ = ["LeshyApp", "LLMUnavailable"]
