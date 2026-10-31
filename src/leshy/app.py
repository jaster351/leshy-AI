from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import uuid
from typing import Any

from .config import Settings
from .diagnostics import configure_diagnostics, event, problem
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
    """Рабочее ядро первой сборки: чат, память, знания, поиск и самообучение."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        configure_diagnostics(self.settings.brain_dir, VERSION)
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
        event(
            "app_ready",
            version=VERSION,
            backend=self.settings.llm_backend,
            model=self.settings.native_chat_file if self.settings.llm_backend == "native" else self.settings.ollama_model,
            brain_dir=self.settings.brain_dir,
        )

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
        event("background_started", learning_enabled=self.learning.is_enabled())

    def stop_background(self) -> None:
        self.self_learning.stop()
        event("background_stopped")

    def _ensure_session(self, owner_id: str, session_id: str | None) -> str:
        owner_id = (owner_id or "web:local").strip()[:200]
        session_id = (session_id or str(uuid.uuid4())).strip()[:200]
        now = utcnow()
        with self.storage.connection() as conn:
            row = conn.execute("SELECT owner_id FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row is not None and str(row["owner_id"]) != owner_id:
                problem("session_owner_mismatch", "Попытка доступа к чужой сессии", session_id=session_id, owner_id=owner_id)
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

    def remember(self, owner_id: str, content: str, *, importance: float = 0.8) -> int:
        content = " ".join(str(content).split()).strip()
        if not content:
            raise ValueError("Память пустая")
        fingerprint = sha256(f"{owner_id}\0{content.lower()}".encode("utf-8")).hexdigest()
        with self.storage.connection() as conn:
            row = conn.execute("SELECT id FROM memories WHERE fingerprint=?", (fingerprint,)).fetchone()
            if row:
                return int(row["id"])
            cur = conn.execute(
                "INSERT INTO memories(owner_id,kind,content,importance,source,created_at,expires_at,fingerprint) VALUES(?,?,?,?,?,?,?,?)",
                (owner_id, "explicit", content, max(0.0, min(float(importance), 1.0)), "user", utcnow(), None, fingerprint),
            )
            memory_id = int(cur.lastrowid)
        self.storage.audit("memory.saved", {"id": memory_id, "owner_id": owner_id, "kind": "explicit"})
        event("memory_saved", memory_id=memory_id, owner_id=owner_id)
        return memory_id

    def memories(self, owner_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT id,kind,content,importance,source,created_at FROM memories WHERE owner_id=? ORDER BY importance DESC,id DESC LIMIT ?",
                (owner_id, max(1, min(int(limit), 100))),
            ).fetchall()
        return [dict(row) for row in rows]

    def _memory_context(self, owner_id: str) -> str:
        items = self.memories(owner_id, limit=8)
        if not items:
            return ""
        return "\n".join(f"- {item['content']}" for item in items)

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
        event("live_search_started", query=query[:500])
        result = self.research.live_query(query, reason="прямой запрос пользователя", limit=limit)
        event("live_search_finished", query=query[:500], status=result.get("status"), results=len(result.get("results", [])))
        return result

    def queue_research(self, query: str) -> int:
        task_id = self.self_learning.queue_topic(query)
        event("research_queued_from_ui", task_id=task_id, query=str(query)[:500])
        return task_id

    def _save_assistant(self, sid: str, answer: str) -> str:
        answered_at = utcnow()
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                (sid, "assistant", answer, answered_at),
            )
            conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (answered_at, sid))
        return answered_at

    def chat(self, message: str, *, owner_id: str = "web:local", session_id: str | None = None) -> dict[str, Any]:
        message = str(message or "").strip()
        owner_id = (owner_id or "web:local").strip()[:200]
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
            count = conn.execute("SELECT COUNT(*) AS c FROM messages WHERE session_id=?", (sid,)).fetchone()["c"]
            if int(count) == 1:
                conn.execute("UPDATE sessions SET title=?,updated_at=? WHERE id=?", (" ".join(message.split())[:80], now, sid))
            rows = conn.execute(
                "SELECT role,content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 24",
                (sid,),
            ).fetchall()

        if message.lower().startswith("/remember "):
            content = message[len("/remember "):].strip()
            memory_id = self.remember(owner_id, content)
            answer = f"Запомнил. Запись памяти #{memory_id}."
            return {"session_id": sid, "answer": answer, "created_at": self._save_assistant(sid, answer), "sources": []}
        if message.lower() == "/memories":
            items = self.memories(owner_id)
            answer = "Сохранённая память пуста." if not items else "Сохранённая память:\n" + "\n".join(f"• {item['content']}" for item in items)
            return {"session_id": sid, "answer": answer, "created_at": self._save_assistant(sid, answer), "sources": []}

        context: list[ChatMessage] = []
        memory_context = self._memory_context(owner_id)
        if memory_context:
            context.append(ChatMessage("system", "Долговременная память этого пользователя:\n" + memory_context))
        trusted = self.knowledge.search(message, trusted_only=True, limit=5)
        if trusted:
            facts = "\n".join(f"- {item['claim']} (источник: {item['source_url']})" for item in trusted)
            context.append(ChatMessage("system", "Проверенные локальные знания Лешего:\n" + facts))

        sources: list[dict[str, str]] = []
        if self._needs_live_search(message):
            try:
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
                elif search_result.get("error"):
                    context.append(ChatMessage("system", "Текущий веб-поиск недоступен: " + str(search_result["error"])))
            except Exception as exc:
                problem("live_search_failed", str(exc), query=message[:500])
                context.append(ChatMessage("system", "Текущий веб-поиск не выполнен: " + str(exc)))

        messages = context + [ChatMessage(str(row["role"]), str(row["content"])) for row in reversed(rows)]
        event("chat_generation_started", session_id=sid, owner_id=owner_id, context_messages=len(messages))
        try:
            answer = self.llm.chat(messages)
        except Exception as exc:
            problem("chat_generation_failed", str(exc), session_id=sid, owner_id=owner_id)
            raise
        answered_at = self._save_assistant(sid, answer)
        event("chat_generation_finished", session_id=sid, owner_id=owner_id, answer_chars=len(answer))
        return {"session_id": sid, "answer": answer, "created_at": answered_at, "sources": sources}

    def set_learning(self, enabled: bool) -> bool:
        value = self.learning.set_enabled(bool(enabled), reason="Web/Telegram")
        self.self_learning.start()
        event("learning_switch", enabled=value)
        return value

    def status(self) -> dict[str, Any]:
        with self.storage.connection() as conn:
            stats = {
                "sessions": int(conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]),
                "messages": int(conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]),
                "memories": int(conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"]),
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
            "logs_dir": str(self.settings.brain_dir / "logs"),
            "learning_enabled": self.learning.is_enabled(),
            "learning_worker": self.self_learning.status(),
            "telegram": telegram_status,
            "stats": stats,
            "hardware": hardware,
        }


__all__ = ["LeshyApp", "LLMUnavailable"]
