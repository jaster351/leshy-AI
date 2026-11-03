from __future__ import annotations

import asyncio
from pathlib import Path
import os
import re
import threading
from typing import Any, Awaitable
from urllib.parse import urlparse

from .browser_agent import BrowserAction, BrowserActionResult, BrowserRisk
from .diagnostics import event, problem
from .system_browser import ensure_system_browser


# Browser Use даёт агенту мощные действия по умолчанию. Эти ограничения должны
# быть техническими, а не только текстовой инструкцией модели.
_ALWAYS_EXCLUDED_ACTIONS: tuple[str, ...] = (
    "evaluate",
    "upload_file",
    "write_file",
    "replace_file",
    "read_file",
)
_READ_ONLY_EXCLUDED_ACTIONS: tuple[str, ...] = (
    "click",
    "input",
    "send_keys",
    "select_dropdown",
)
_PROFILE_PART_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


class BrowserUseAdapter:
    """Интеллектуальный Browser Agent поверх Browser Use.

    READ выполняется автоматически, но получает урезанный набор Browser Use tools:
    без click/input/send_keys/form selection, произвольного JavaScript и файловых
    операций. Любое изменение допускается только после `_approval_verified`, который
    может поставить исключительно BrowserAgent.

    По умолчанию используется общий профиль Browser Agent. Специализированные
    модули могут передать безопасные `profile_namespace` и `profile_key`; тогда
    создаётся отдельный постоянный профиль внутри LeshyBrain/browser/profiles.
    Это используется, например, для изоляции личных кабинетов разных пользователей.
    """

    adapter_id = "browser-use"

    def __init__(self, llm: Any | None = None, *, max_steps: int = 40, headless: bool | None = None):
        self.llm = llm
        self.max_steps = max(3, min(int(max_steps), 100))
        if headless is None:
            raw = os.getenv("LESHY_BROWSER_HEADLESS", "1").strip().lower()
            headless = raw not in {"0", "false", "no", "off"}
        self.headless = bool(headless)
        self.root = Path(os.getenv("LESHY_BRAIN_DIR", "LeshyBrain")).expanduser()
        self.profile_dir = self.root / "browser" / "browser-use-profile"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.profile_dir.chmod(0o700)
        except Exception:
            pass

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
        explicit = tuple(dict.fromkeys(str(x).lower().strip().strip(".") for x in raw if str(x).strip()))
        if explicit:
            return explicit
        primary = (urlparse(action.url).hostname or "").lower().strip(".")
        return (primary,) if primary else ()

    @staticmethod
    def _host_allowed(url: str, hosts: tuple[str, ...]) -> bool:
        host = (urlparse(str(url or "")).hostname or "").lower().strip(".")
        return bool(host) and any(host == item or host.endswith("." + item) for item in hosts)

    @staticmethod
    def _domain_patterns(hosts: tuple[str, ...]) -> list[str]:
        patterns: list[str] = []
        for host in hosts:
            patterns.append(host)
            patterns.append(f"*.{host}")
        return patterns

    @staticmethod
    def _excluded_actions(action: BrowserAction) -> tuple[str, ...]:
        blocked = list(_ALWAYS_EXCLUDED_ACTIONS)
        if action.risk is BrowserRisk.READ:
            blocked.extend(_READ_ONLY_EXCLUDED_ACTIONS)
        return tuple(dict.fromkeys(blocked))

    @classmethod
    def _tools_for_action(cls, action: BrowserAction):
        from browser_use import Tools

        return Tools(exclude_actions=list(cls._excluded_actions(action)))

    def _profile_for_action(self, action: BrowserAction) -> Path:
        namespace = str(action.arguments.get("profile_namespace") or "").strip()
        key = str(action.arguments.get("profile_key") or "").strip()
        if not namespace and not key:
            return self.profile_dir
        if not namespace or not key:
            raise ValueError("Изолированный browser-профиль требует profile_namespace и profile_key")
        if not _PROFILE_PART_RE.fullmatch(namespace) or not _PROFILE_PART_RE.fullmatch(key):
            raise ValueError("Некорректный ключ изолированного browser-профиля")
        target = self.root / "browser" / "profiles" / namespace / key
        target.mkdir(parents=True, exist_ok=True)
        try:
            target.chmod(0o700)
        except Exception:
            pass
        return target

    @staticmethod
    def _run_sync(awaitable: Awaitable[Any]) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)

        result: dict[str, Any] = {}
        error: dict[str, BaseException] = {}

        def worker() -> None:
            try:
                result["value"] = asyncio.run(awaitable)
            except BaseException as exc:  # pragma: no cover
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
                "РЕЖИМ ТОЛЬКО ЧТЕНИЯ: можно искать, открывать URL, прокручивать, сравнивать и извлекать данные. "
                "Инструменты click/input/send_keys/select_dropdown/JS/file I/O технически отключены. "
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
        if not self._host_allowed(action.url, hosts):
            return BrowserActionResult("blocked", "Стартовый URL не входит в разрешённые домены")

        browser_info = ensure_system_browser(auto_install=True)
        profile_dir = self._profile_for_action(action)
        event(
            "browser_agent_browser_launch_config",
            browser=browser_info.name,
            executable_name=Path(browser_info.executable).name,
            headless=self.headless,
            isolated_profile=profile_dir != self.profile_dir,
            profile_has_lock=any((profile_dir / name).exists() for name in ("SingletonLock", "lockfile")),
            action=action.name,
        )
        browser = Browser(
            executable_path=browser_info.executable,
            user_data_dir=str(profile_dir),
            headless=self.headless,
            allowed_domains=self._domain_patterns(hosts),
            keep_alive=False,
        )
        user_task = str(action.arguments.get("task") or action.description or "").strip()
        if not user_task:
            user_task = f"Изучи страницу {action.url} и верни полезную информацию."

        provider = str(getattr(self.llm, "provider", "") or "").lower()
        default_vision = provider != "leshy"
        excluded = self._excluded_actions(action)
        event(
            "browser_agent_browser_use_tools",
            action=action.name,
            risk=action.risk.value,
            excluded=list(excluded),
            isolated_profile=profile_dir != self.profile_dir,
        )
        agent = Agent(
            task=user_task + self._guard(action, hosts),
            llm=self.llm,
            browser=browser,
            tools=self._tools_for_action(action),
            use_vision=bool(action.arguments.get("use_vision", default_vision)),
        )
        try:
            history = await agent.run(
                max_steps=min(self.max_steps, int(action.arguments.get("max_steps", self.max_steps)))
            )
            final = history.final_result() if hasattr(history, "final_result") else str(history)
            event(
                "browser_agent_browser_use_completed",
                hosts=list(hosts),
                risk=action.risk.value,
                action=action.name,
                isolated_profile=profile_dir != self.profile_dir,
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
        hosts = self._hosts(action)
        if not hosts or not self._host_allowed(action.url, hosts):
            return BrowserActionResult("blocked", "Стартовый URL не входит в разрешённые домены")
        if not self.available():
            return BrowserActionResult("unavailable", "Browser Use или совместимая LLM недоступны")
        try:
            return self._run_sync(self._run(action))
        except Exception as exc:
            problem("browser_agent_browser_use_failed", str(exc), risk=action.risk.value, action=action.name)
            return BrowserActionResult("failed", str(exc))


__all__ = ["BrowserUseAdapter"]
