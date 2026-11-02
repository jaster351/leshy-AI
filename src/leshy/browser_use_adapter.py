from __future__ import annotations

import asyncio
from pathlib import Path
import os
import threading
from typing import Any, Awaitable
from urllib.parse import urlparse

from .browser_agent import BrowserAction, BrowserActionResult, BrowserRisk
from .diagnostics import event, problem
from .system_browser import ensure_system_browser


class BrowserUseAdapter:
    """Интеллектуальный Browser Agent поверх Browser Use.

    По умолчанию работает только на чтение. Изменяющие/commit/financial задачи
    принимаются исключительно с `_approval_verified`, который ставит BrowserAgent
    после Human Approval Gate. Это позволяет использовать Browser Use на сайтах с
    динамическим DOM, сохраняя Playwright как детерминированный исполнитель для
    известных и проверенных workflow.
    """

    adapter_id = "browser-use"

    def __init__(self, llm: Any | None = None, *, max_steps: int = 40, headless: bool | None = None):
        self.llm = llm
        self.max_steps = max(3, min(int(max_steps), 100))
        if headless is None:
            raw = os.getenv("LESHY_BROWSER_HEADLESS", "1").strip().lower()
            headless = raw not in {"0", "false", "no", "off"}
        self.headless = bool(headless)
        root = Path(os.getenv("LESHY_BRAIN_DIR", "LeshyBrain")).expanduser()
        self.profile_dir = root / "browser" / "browser-use-profile"
        self.profile_dir.mkdir(parents=True, exist_ok=True)

    def available(self) -> bool:
        if self.llm is None:
            return False
        try:
            import browser_use  # noqa: F401
            return True
        except Exception:
            return False

    @staticmethod
    def _hosts(action: BrowserAction) -> tuple[str, ...]:
        raw = action.arguments.get("allowed_hosts") or ()
        if isinstance(raw, str):
            raw = [raw]
        hosts = [str(x).lower().strip().strip(".") for x in raw if str(x).strip()]
        primary = (urlparse(action.url).hostname or "").lower().strip(".")
        if primary and primary not in hosts:
            hosts.append(primary)
        return tuple(dict.fromkeys(hosts))

    @staticmethod
    def _domain_patterns(hosts: tuple[str, ...]) -> list[str]:
        patterns: list[str] = []
        for host in hosts:
            patterns.append(host)
            patterns.append(f"*.{host}")
        return patterns

    @staticmethod
    def _run_sync(awaitable: Awaitable[Any]) -> Any:
        """Запустить async Browser Use из sync tool API, включая активный event loop."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)

        result: dict[str, Any] = {}
        error: dict[str, BaseException] = {}

        def worker() -> None:
            try:
                result["value"] = asyncio.run(awaitable)
            except BaseException as exc:  # pragma: no cover - передаётся вызывающему потоку
                error["value"] = exc

        thread = threading.Thread(target=worker, name="leshy-browser-use", daemon=True)
        thread.start()
        thread.join()
        if error:
            raise error["value"]
        return result.get("value")

    @staticmethod
    def _guard(action: BrowserAction, hosts: tuple[str, ...]) -> str:
        common = (
            "\n\nОБЯЗАТЕЛЬНЫЕ ОГРАНИЧЕНИЯ ЛЕШЕГО: "
            "не обходи CAPTCHA, challenge, 2FA или подтверждение присутствия человека; "
            "если они появились — остановись и сообщи пользователю. Не переходи на домены вне списка. "
            "Не раскрывай пароли, токены, cookies, CVV и другие секреты в результате или логах. "
            f"Начальный URL: {action.url}. Разрешённые домены: {', '.join(hosts)}. "
        )
        if action.risk is BrowserRisk.READ:
            return common + (
                "РЕЖИМ ТОЛЬКО ЧТЕНИЯ: можно искать, открывать страницы, сравнивать и извлекать данные. "
                "Нельзя входить в аккаунт, менять данные, добавлять товар в корзину, отправлять формы, "
                "бронировать, публиковать, оформлять заказ или выполнять финансовое действие."
            )
        if action.risk is BrowserRisk.MUTATE:
            return common + (
                "Пользователь подтвердил изменение данных, описанное в задаче. Выполняй только это изменение. "
                "Нельзя оформлять заказ/бронирование, принимать финансовое обязательство или проводить оплату."
            )
        if action.risk is BrowserRisk.COMMIT:
            return common + (
                "Пользователь явно подтвердил внешнее действие, описанное в задаче. Разрешено выполнить только "
                "указанное бронирование/заказ/публикацию. Если появляется списание денег или новый платёжный шаг, "
                "остановись ДО него: финансовое действие требует отдельного подтверждения."
            )
        summary = str(action.arguments.get("approval_summary") or "").strip()
        return common + (
            "Пользователь отдельно подтвердил финансовое действие. Выполни только транзакцию из подтверждённого "
            f"резюме: {summary}. Не меняй сумму, валюту, получателя, количество или дополнительные услуги. "
            "При любом отличии остановись без оплаты."
        )

    async def _run(self, action: BrowserAction) -> BrowserActionResult:
        from browser_use import Agent, Browser

        hosts = self._hosts(action)
        if not hosts:
            return BrowserActionResult("blocked", "Browser Use требует ограниченный список доменов")

        browser_info = ensure_system_browser(auto_install=True)
        browser = Browser(
            executable_path=browser_info.executable,
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            allowed_domains=self._domain_patterns(hosts),
            keep_alive=False,
        )

        user_task = str(action.arguments.get("task") or action.description or "").strip()
        if not user_task:
            user_task = f"Изучи страницу {action.url} и верни полезную информацию."

        provider = str(getattr(self.llm, "provider", "") or "").lower()
        default_vision = provider != "leshy"
        agent = Agent(
            task=user_task + self._guard(action, hosts),
            llm=self.llm,
            browser=browser,
            use_vision=bool(action.arguments.get("use_vision", default_vision)),
        )
        try:
            history = await agent.run(max_steps=min(self.max_steps, int(action.arguments.get("max_steps", self.max_steps))))
            final = history.final_result() if hasattr(history, "final_result") else str(history)
            event(
                "browser_agent_browser_use_completed",
                hosts=list(hosts),
                risk=action.risk.value,
                action=action.name,
            )
            return BrowserActionResult(
                "ok",
                "Browser Use завершил браузерную задачу.",
                {"result": str(final or ""), "allowed_hosts": list(hosts)},
            )
        finally:
            try:
                close = getattr(browser, "stop", None) or getattr(browser, "close", None)
                if close is not None:
                    value = close()
                    if hasattr(value, "__await__"):
                        await value
            except Exception:
                pass

    def execute(self, action: BrowserAction) -> BrowserActionResult:
        supported = {"research", "agent_read", "agent_mutate", "agent_commit", "agent_financial"}
        if action.name not in supported:
            return BrowserActionResult("unsupported", "Browser Use не поддерживает этот тип browser-действия")
        if action.risk is not BrowserRisk.READ and not bool(action.arguments.get("_approval_verified")):
            return BrowserActionResult("blocked", "Изменяющее browser-действие не прошло Human Approval Gate")
        if action.risk is BrowserRisk.FINANCIAL and not str(action.arguments.get("approval_summary") or "").strip():
            return BrowserActionResult("blocked", "Финансовое действие требует подтверждённого резюме суммы и условий")
        if not self.available():
            return BrowserActionResult("unavailable", "Browser Use или совместимая LLM недоступны")
        try:
            return self._run_sync(self._run(action))
        except Exception as exc:
            problem("browser_agent_browser_use_failed", str(exc), risk=action.risk.value, action=action.name)
            return BrowserActionResult("failed", str(exc))


__all__ = ["BrowserUseAdapter"]
