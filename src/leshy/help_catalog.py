from __future__ import annotations

import re
from typing import Any

from .storage import utcnow
from .version import VERSION


_HELP_PHRASES = {
    "help",
    "/help",
    "помощь",
    "/помощь",
    "что ты умеешь",
    "что умеешь",
    "что ты можешь",
    "что можешь",
    "как с тобой работать",
    "как тобой пользоваться",
    "как работать с тобой",
    "справка",
    "/справка",
}


def _normalize(text: str) -> str:
    value = str(text or "").strip().lower().replace("ё", "е")
    value = re.sub(r"[?!.,:;]+$", "", value).strip()
    value = re.sub(r"\s+", " ", value)
    return value


def is_help_request(text: str) -> bool:
    return _normalize(text) in {_normalize(item) for item in _HELP_PHRASES}


def build_help_text() -> str:
    return f"""Леший {VERSION} — справочник

КАК СО МНОЙ РАБОТАТЬ

Пишите обычным человеческим языком. Можно задавать вопрос, просить найти актуальную информацию, объяснить тему, запомнить факт, открыть сайт, выполнить допустимое действие на компьютере или использовать подключённый модуль. Чем конкретнее цель и ограничения, тем точнее результат.

Примеры:
• «Найди актуальную информацию о ...»
• «Сравни эти варианты и объясни, какой лучше»
• «Запомни, что ...»
• «Открой сайт и найди ...»
• «Помоги разобраться с этим файлом»
• «Что ты умеешь?»

Для опасных, финансовых или необратимых действий я должен запросить подтверждение. CAPTCHA, 2FA и другие защитные механизмы я не обхожу.

ЧТО Я УМЕЮ СЕЙЧАС

1. Локальный чат
Работаю через локальную LLM. Поддерживаю обычный диалог, историю сессий и несколько пользователей с раздельными данными.

2. Долговременная память
Команда `/remember текст` сохраняет факт. Команда `/memories` показывает сохранённую память текущего пользователя.

3. Интернет и актуальная информация
Могу выполнять live-search и исследование интернета. Явная команда: `/search запрос`. Новые сведения проходят путь research → candidate → verification → trusted и не считаются доверенными автоматически.

4. Управляемое самообучение
Самообучение можно включать и выключать. В выключенном состоянии автономное фоновое исследование не запускается, но прямой поиск пользователя остаётся доступен.

5. Web UI
Есть локальный веб-интерфейс, история диалогов, системные панели и доступ по сети при соответствующей настройке firewall/API token.

6. Telegram
Могу работать через Telegram long polling. Поддерживаются отдельные пользовательские пространства, статус и управление самообучением.

7. Browser Agent
Интеллектуальная работа в браузере строится на Browser Use + Playwright. Могу исследовать сайты и выполнять детерминированные browser-workflow. Покупки, бронирования и другие необратимые действия проходят Human Approval Gate.

8. Computer Use
Есть отдельный модуль управления компьютером. Он развивается и не считается полностью завершённым для всех сценариев.

9. Работа с документами
Document Intelligence использует MarkItDown и умеет извлекать содержимое из PDF, DOCX, XLSX, PPTX и других поддерживаемых форматов.

10. Marketplace и расширения
Есть Extension Marketplace, MCP v2 Tools Gateway и система Skills/модулей. Расширения устанавливаются изолированно с проверкой разрешений, версии, лицензии и health-check.

11. Голос
В проекте есть ASR/TTS, голосовые настройки и голосовой UI. Голосовые функции продолжают развиваться и могут отличаться по готовности в разных сборках.

12. Изображения
Есть локальный модуль генерации изображений. При первом использовании модель может быть загружена отдельно.

13. Аппаратное ускорение
Поддерживается выбор backend в порядке GPU/Vulkan → NPU/OpenVINO → CPU с автоматическим fallback, если ускоритель недоступен.

14. Диагностика
Есть централизованные логи, redaction секретов, журнал проблем, runtime-диагностика и проверки готовности сборки.

ЭКСПЕРИМЕНТАЛЬНО

• «Аквариум Лешего» разрабатывается отдельной веткой/PR как виртуальный питомец и симулятор пресноводной/морской экосистемы. Пока эта функция может отсутствовать в стабильной `main`-сборке.

ПОЛЕЗНЫЕ КОМАНДЫ

`help`, `/help`, `помощь`, `что ты умеешь`, `как с тобой работать` — открыть этот справочник.
`/search запрос` — явный интернет-поиск.
`/remember текст` — сохранить факт в память.
`/memories` — показать сохранённую память.
`/status` — статус в Telegram, если Telegram-модуль включён.
`/learning_on` — включить самообучение в Telegram.
`/learning_off` — выключить самообучение в Telegram.

Подробная установка, сборка и развёртывание для Linux, macOS и Windows: `docs/HELP_AND_INSTALLATION.md`.
"""


def install_help_routing(app_cls: type[Any]) -> None:
    """Добавляет детерминированный help-ответ без обращения к LLM."""
    if getattr(app_cls, "_leshy_help_routing_installed", False):
        return

    original_chat = app_cls.chat

    def chat(self, message: str, *, owner_id: str = "web:local", session_id: str | None = None):
        if not is_help_request(message):
            return original_chat(self, message, owner_id=owner_id, session_id=session_id)

        message_value = str(message or "").strip()
        owner_value = (owner_id or "web:local").strip()[:200]
        sid = self._ensure_session(owner_value, session_id)
        now = utcnow()
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                (sid, "user", message_value, now),
            )
            count = conn.execute("SELECT COUNT(*) AS c FROM messages WHERE session_id=?", (sid,)).fetchone()["c"]
            if int(count) == 1:
                conn.execute("UPDATE sessions SET title=?,updated_at=? WHERE id=?", (message_value[:80], now, sid))

        answer = build_help_text()
        return {
            "session_id": sid,
            "answer": answer,
            "created_at": self._save_assistant(sid, answer),
            "sources": [],
        }

    app_cls.chat = chat
    app_cls._leshy_help_routing_installed = True


__all__ = ["build_help_text", "install_help_routing", "is_help_request"]
