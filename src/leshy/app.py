from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import re
import uuid
from typing import Any

from .accelerator_runtime import LlamaCppRuntimeManager, LlamaServerLLM
from .adaptive_backend import AdaptiveLLM
from .config import Settings
from .cinema_research import cinema_query, cinema_instruction, cinema_search, cinema_no_data
from .diagnostics import configure_diagnostics, event, problem
from .hardware import detect_hardware
from .image_generation import ImageGenerationManager
from .knowledge import KnowledgeBase
from .learning_control import LearningControl
from .llm import ChatMessage, EchoLLM, LLMUnavailable, OllamaLLM
from .model_download import ModelDownloader
from .native_chat import NativeGGUFLLM
from .research import DdgsSearch, FallbackSearch, ResearchEngine, SearxngSearch
from .research_policy import research_reason
from .self_learning import SelfLearningWorker
from .storage import Storage, utcnow
from .version import VERSION


class LeshyApp:
    """Рабочее ядро: чат, память, знания, поиск, обучение и локальные инструменты."""

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
        self.hardware = self._detect_hardware()
        self.image_generation = ImageGenerationManager(self.settings.brain_dir, self.hardware)
        self.model_path = self.settings.native_models_dir / self.settings.native_chat_file
        self.model_downloader = ModelDownloader(self.model_path)
        self.accelerator_runtime = LlamaCppRuntimeManager(self.settings.brain_dir)
        self.llm = self._build_llm()
        self.self_learning = SelfLearningWorker(self.storage, self.learning, self.research)
        self.telegram = None
        event(
            "app_ready",
            version=VERSION,
            backend=self.settings.llm_backend,
            model=self.settings.native_chat_file if self.settings.llm_backend == "native" else self.settings.ollama_model,
            brain_dir=self.settings.brain_dir,
            gpu_names=list(getattr(self.hardware, "gpu_names", ())),
            npu_names=list(getattr(self.hardware, "npu_names", ())),
        )

    @staticmethod
    def _detect_hardware():
        try:
            return detect_hardware()
        except Exception as exc:
            problem("hardware_detection_failed", str(exc))
            return None

    def _require_model(self) -> Path:
        try:
            return self.model_downloader.ensure_verified()
        except RuntimeError as exc:
            raise LLMUnavailable(str(exc)) from exc

    def _build_llm(self):
        backend = self.settings.llm_backend
        if backend == "echo":
            return EchoLLM()
        if backend == "ollama":
            return OllamaLLM(self.settings.ollama_url, self.settings.ollama_model)
        if backend != "native":
            raise ValueError(f"Неизвестный LLM backend: {backend}")

        cpu_llm = NativeGGUFLLM(self.model_path, n_ctx=self.settings.native_context_size, n_gpu_layers=0)
        cpu_server = LlamaServerLLM(
            self.accelerator_runtime,
            "cpu",
            self.model_path,
            ensure_model=self._require_model,
            n_ctx=min(self.settings.native_context_size, 4096),
            n_gpu_layers=0,
            startup_timeout=45,
            request_timeout=180,
        )
        candidates: list[tuple[str, Any]] = []
        caps = self.hardware

        if caps is not None and bool(getattr(caps, "gpu", False)):
            candidates.append((
                "gpu-vulkan",
                LlamaServerLLM(
                    self.accelerator_runtime,
                    "vulkan",
                    self.model_path,
                    ensure_model=self._require_model,
                    n_ctx=self.settings.native_context_size,
                    n_gpu_layers=-1,
                    gpu_count=max(1, len(getattr(caps, "gpu_names", ()) or ())),
                    startup_timeout=45,
                    request_timeout=120,
                ),
            ))

        if caps is not None and bool(getattr(caps, "npu", False)):
            candidates.append((
                "npu-openvino",
                LlamaServerLLM(
                    self.accelerator_runtime,
                    "openvino",
                    self.model_path,
                    ensure_model=self._require_model,
                    n_ctx=min(self.settings.native_context_size, 2048),
                    n_gpu_layers=0,
                    device="NPU",
                    startup_timeout=30,
                    request_timeout=120,
                ),
            ))

        # Fresh official llama-server is a robust CPU fallback when the bundled
        # Python binding cannot open a newly released GGUF architecture/format.
        candidates.append(("cpu-llama-server", cpu_server))
        candidates.append(("cpu-native", cpu_llm))
        return AdaptiveLLM(
            candidates,
            cooldown_seconds=180,
            priority=("gpu", "npu", "cpu", "other"),
        )

    def start_background(self) -> None:
        self.self_learning.start()
        event("background_started", learning_enabled=self.learning.is_enabled())

    def stop_background(self) -> None:
        self.self_learning.stop()
        closer = getattr(self.llm, "close", None)
        if callable(closer):
            closer()
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
        return "\n".join(f"- {item['content']}" for item in items) if items else ""

    @staticmethod
    def _image_prompt(text: str) -> str | None:
        value = " ".join(str(text or "").split()).strip()
        lower = value.lower()
        image_nouns = ("картинк", "изображен", "фото", "рисунок", "image", "picture")
        generate_verbs = ("сгенерир", "нарисуй", "нарисовать", "создай", "создать", "generate", "draw", "create")
        if not (any(noun in lower for noun in image_nouns) and any(verb in lower for verb in generate_verbs)):
            return None
        prompt = re.sub(
            r"(?i)\b(сгенерируй|сгенерировать|создай|создать|нарисуй|нарисовать|generate|draw|create)\b",
            " ",
            value,
        )
        prompt = re.sub(r"(?i)\b(мне|пожалуйста)\b", " ", prompt)
        prompt = re.sub(r"(?i)\b(картинку|картинка|изображение|изображения|фото|рисунок|image|picture)\b", " ", prompt)
        prompt = " ".join(prompt.strip(" .,:;-—").split())
        if not prompt:
            prompt = "beautiful high quality image"
        # SD 1.5 understands English much better. Preserve arbitrary user text,
        # but improve the very common Russian New Year request without making
        # image generation depend on the text LLM being healthy.
        replacements = {
            "новогодней елки": "beautiful decorated Christmas tree, New Year, festive lights, winter atmosphere",
            "новогоднюю елку": "beautiful decorated Christmas tree, New Year, festive lights, winter atmosphere",
            "новогодняя елка": "beautiful decorated Christmas tree, New Year, festive lights, winter atmosphere",
            "новогодней ёлки": "beautiful decorated Christmas tree, New Year, festive lights, winter atmosphere",
            "новогоднюю ёлку": "beautiful decorated Christmas tree, New Year, festive lights, winter atmosphere",
            "новогодняя ёлка": "beautiful decorated Christmas tree, New Year, festive lights, winter atmosphere",
        }
        return replacements.get(prompt.lower(), prompt)

    @staticmethod
    def _needs_live_search(text: str) -> bool:
        value = text.lower()
        markers = (
            "/search ", "найди в интернете", "поищи в интернете", "проверь в интернете",
            "актуальн", "сегодня", "сейчас", "последн", "новый", "новая", "новое", "новые",
            "вышел", "вышла", "вышло", "последняя версия", "текущая цена", "курс валют", "погода",
        )
        return any(marker in value for marker in markers)

    @staticmethod
    def _answer_needs_research(answer: str) -> bool:
        value = " ".join(str(answer or "").lower().split())
        markers = (
            "у меня нет доступа к интернету",
            "нет доступа к интернету",
            "у меня нет доступа к текущим новостям",
            "в моих данных нет",
            "у меня нет информации",
            "у меня нет актуальной информации",
            "я не знаю",
            "не располагаю информацией",
            "не могу проверить",
            "не могу узнать",
            "предложить ресурсы для самостоятельного поиска",
            "самостоятельного поиска",
        )
        return any(marker in value for marker in markers)

    def live_search(self, query: str, *, limit: int = 5, reason: str = "прямой запрос пользователя") -> dict:
        query = " ".join(str(query).replace("/search", "", 1).split()).strip()
        if not query:
            raise ValueError("Пустой поисковый запрос")
        event("live_search_started", query=query[:500], reason=reason[:200])
        result = self.research.live_query(query, reason=reason, limit=limit)
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

        image_prompt = self._image_prompt(message)
        if image_prompt is not None:
            job = self.image_generation.enqueue(image_prompt)
            answer = (
                "Начал локальную генерацию изображения. При первом запуске Леший сам скачает и проверит модель "
                "изображений; прогресс появится прямо в чате."
            )
            return {
                "session_id": sid,
                "answer": answer,
                "created_at": self._save_assistant(sid, answer),
                "sources": [],
                "image_job": job,
            }

        context: list[ChatMessage] = [
            ChatMessage(
                "system",
                "Интернет-поиск Лешему доступен через приложение. Никогда не утверждай, что доступа к интернету нет, "
                "и не отправляй пользователя искать информацию самостоятельно. Если приложением переданы результаты "
                "веб-поиска, используй их для ответа и укажи неопределённость там, где она есть. Если запрос неоднозначен "
                "и нельзя понять, о каком человеке или объекте речь, задай одно короткое уточнение вместо выдумывания фактов.",
            )
        ]
        memory_context = self._memory_context(owner_id)
        if memory_context:
            context.append(ChatMessage("system", "Долговременная память этого пользователя:\n" + memory_context))

        trusted = self.knowledge.search(message, trusted_only=True, limit=5)
        candidates = self.knowledge.search_candidates(message, limit=5)
        if trusted:
            facts = "\n".join(f"- {item['claim']} (источник: {item['source_url']})" for item in trusted)
            context.append(ChatMessage("system", "Проверенные локальные знания Лешего:\n" + facts))

        cinema_plan = cinema_query(message, rows)
        if cinema_plan is not None:
            context.append(ChatMessage("system", cinema_instruction(cinema_plan)))
        sources: list[dict[str, str]] = []
        search_attempted = False
        search_error = ""

        def append_live_search(reason_text: str) -> None:
            nonlocal sources, search_attempted, search_error
            search_attempted = True
            try:
                search_result = (cinema_search(self, cinema_plan) if cinema_plan is not None
                                 else self.live_search(message, limit=5, reason=reason_text))
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
                        "Результаты текущего веб-поиска. Они уже сохранены в локальную базу как candidate-знания и "
                        "поставлены на верификацию. Отвечай по этим данным, не утверждая непроверенное как бесспорный факт:\n"
                        + snippets,
                    ))
                elif search_result.get("error"):
                    search_error = str(search_result["error"])
                    context.append(ChatMessage("system", "Текущий веб-поиск временно не выполнен: " + search_error))
            except Exception as exc:
                search_error = str(exc)
                problem("live_search_failed", search_error, query=message[:500])
                context.append(ChatMessage("system", "Текущий веб-поиск временно не выполнен: " + search_error))

        reason = research_reason(message, trusted_count=len(trusted), candidate_count=len(candidates))
        if cinema_plan is not None or reason or self._needs_live_search(message):
            append_live_search(reason or "требуются актуальные данные")

        if cinema_plan is not None and not sources:
            answer = cinema_no_data(cinema_plan)
            return {"session_id": sid, "answer": answer, "created_at": self._save_assistant(sid, answer), "sources": []}

        history_messages = [ChatMessage(str(row["role"]), str(row["content"])) for row in reversed(rows)]
        messages = context + history_messages
        event("chat_generation_started", session_id=sid, owner_id=owner_id, context_messages=len(messages))
        try:
            answer = self.llm.chat(messages)
            if self._answer_needs_research(answer):
                if not search_attempted:
                    append_live_search("модель обнаружила пробел в знаниях")
                if sources:
                    retry_context = context + [ChatMessage(
                        "system",
                        "Предыдущий черновик ответа ошибочно сообщил об отсутствии данных или доступа к интернету. "
                        "Сформируй новый ответ по найденным веб-источникам. Не советуй пользователю искать самому.",
                    )]
                    answer = self.llm.chat(retry_context + history_messages)
                elif search_attempted and search_error:
                    answer = "Не удалось получить актуальные данные из интернета прямо сейчас: " + search_error
                elif search_attempted:
                    answer = "Я выполнил интернет-поиск, но не нашёл достаточно данных для уверенного ответа. Уточните, пожалуйста, о ком или о чём именно идёт речь."
        except Exception as exc:
            problem("chat_generation_failed", str(exc), session_id=sid, owner_id=owner_id)
            raise
        answered_at = self._save_assistant(sid, answer)
        event(
            "chat_generation_finished",
            session_id=sid,
            owner_id=owner_id,
            answer_chars=len(answer),
            web_search=search_attempted,
            web_sources=len(sources),
        )
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
        hardware = asdict(self.hardware) if self.hardware is not None else {"error": "hardware detection failed"}
        router_status = getattr(self.llm, "status", None)
        model_status = self.model_downloader.status()
        return {
            "name": "Леший",
            "version": VERSION,
            "backend": self.settings.llm_backend,
            "model": getattr(self.llm, "model", self.settings.ollama_model),
            "model_ready": bool(model_status.get("exists")) and model_status.get("state") not in {"invalid", "error", "downloading"} if self.settings.llm_backend == "native" else True,
            "model_download": model_status,
            "image_generation": self.image_generation.status(),
            "brain_dir": str(self.settings.brain_dir),
            "logs_dir": str(self.settings.brain_dir / "logs"),
            "learning_enabled": self.learning.is_enabled(),
            "learning_worker": self.self_learning.status(),
            "telegram": self.telegram.status() if self.telegram is not None else {"configured": False, "running": False},
            "stats": stats,
            "hardware": hardware,
            "chat_router": router_status() if callable(router_status) else None,
        }


__all__ = ["LeshyApp", "LLMUnavailable"]
