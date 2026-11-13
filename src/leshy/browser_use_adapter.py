from __future__ import annotations

import asyncio
from pathlib import Path
import os
import re
import threading
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from .browser_agent import BrowserAction, BrowserActionResult, BrowserRisk
from .diagnostics import event, problem
from .browser_profile_cdp import profile_cdp_url, profile_may_be_in_use, profile_busy_message
from .browser_launch_policy import chromium_launch_policy
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
        launch_policy = chromium_launch_policy()
        self.headless = launch_policy.headless if headless is None else (
            bool(headless) if launch_policy.can_open_window else True
        )
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
    def _run_sync(make_awaitable: Callable[[], Awaitable[Any]]) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(make_awaitable())

        result: dict[str, Any] = {}
        error: dict[str, BaseException] = {}

        def worker() -> None:
            try:
                result["value"] = asyncio.run(make_awaitable())
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
                "бронировать, публиковать, оформлять заказ или выполнять финансовое действие. "
                "После чтения обязательно верни содержательный итог: название страницы, краткое "
                "резюме и ключевые факты. Не ограничивайся фразой об успешном открытии. "
                "Если текст не удалось получить, прямо сообщи об этом."
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

    @staticmethod
    def _result_for_user(
        action: BrowserAction, final: Any, hosts: tuple[str, ...]
    ) -> BrowserActionResult:
        """Вернуть пользователю результат браузера, а не служебную квитанцию."""
        content = str(final or "").strip()
        data = {"result": content, "allowed_hosts": list(hosts), "url": action.url}
        # Короткое содержательное сообщение допустимо; пустой отчёт — нет.
        generic = re.fullmatch(
            r"(?is)(?:готово[.!]*|успешно[.!]*|done[.!]*|completed[.!]*|"
            r"страница (?:прочитана|открыта)[.!]*|"
            r"browser use завершил браузерную задачу[.!]*)",
            content,
        )
        if not content or (action.risk is BrowserRisk.READ and generic):
            return BrowserActionResult(
                "failed",
                "Браузер завершил работу, но не вернул содержимое страницы или полезный итог.",
                data,
            )
        if action.risk is BrowserRisk.READ:
            visible = f"Результат просмотра {action.url}:\n{content[:3500]}"
        else:
            visible = content[:3500]
        return BrowserActionResult("ok", visible, data)

    async def _run(self, action: BrowserAction) -> BrowserActionResult:
        from browser_use import Agent, Browser

        hosts = self._hosts(action)
        if not hosts:
            return BrowserActionResult("blocked", "Browser Use требует ограниченный список доменов")
        if not self._host_allowed(action.url, hosts):
            return BrowserActionResult("blocked", "Стартовый URL не входит в разрешённые домены")

        browser_info = ensure_system_browser(auto_install=True)
        profile_dir = self._profile_for_action(action)
        # «Лента» использует отдельное постоянно открытое окно авторизации.
        # Подключение к живому CDP сохраняет cookies и не конфликтует с lock
        # user-data-dir. К чужим профилям не подключаемся.
        is_lenta = str(action.arguments.get("profile_namespace") or "") == "lenta"
        cdp_url = profile_cdp_url(profile_dir) if is_lenta else None
        if is_lenta and not cdp_url and profile_may_be_in_use(profile_dir):
            return BrowserActionResult("failed", profile_busy_message())
        event(
            "browser_agent_browser_launch_config",
            browser=browser_info.name,
            executable_name=Path(browser_info.executable).name,
            headless=self.headless,
            isolated_profile=profile_dir != self.profile_dir,
            profile_has_lock=profile_may_be_in_use(profile_dir),
            attached_to_existing_window=bool(cdp_url),
            action=action.name,
            root_linux=chromium_launch_policy().root_linux,
        )
        if cdp_url:
            browser = Browser(
                cdp_url=cdp_url,
                allowed_domains=self._domain_patterns(hosts),
                keep_alive=True,
            )
        else:
            policy = chromium_launch_policy()
            launch_options = {}
            if policy.args or not policy.chromium_sandbox:
                launch_options = {
                    "chromium_sandbox": policy.chromium_sandbox,
                    "args": list(policy.args),
                }
            browser = Browser(
                executable_path=browser_info.executable,
                user_data_dir=str(profile_dir),
                headless=self.headless,
                allowed_domains=self._domain_patterns(hosts),
                keep_alive=False,
                **launch_options,
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
            response = self._result_for_user(action, final, hosts)
            event(
                "browser_agent_browser_use_completed",
                hosts=list(hosts),
                risk=action.risk.value,
                action=action.name,
                isolated_profile=profile_dir != self.profile_dir,
                result_status=response.status,
                result_chars=len(str(final or "")),
            )
            return response
        finally:
            try:
                close = getattr(browser, "stop", None) or getattr(browser, "close", None)
                if close is not None:
                    value = close()
                    if hasattr(value, "__await__"):
                        await value
            except Exception:
                pass

    def _fallback_read(self, action: BrowserAction, cause: str) -> BrowserActionResult:
        """Read-only fallback when Browser Use or Edge/CDP cannot start.

        Never retry MUTATE/COMMIT/FINANCIAL with another profile: that would
        silently lose the user's authenticated session and approval context.
        """
        from .playwright_browser_adapter import PlaywrightBrowserAdapter

        fallback = PlaywrightBrowserAdapter(headless=self.headless)
        result = fallback.execute(BrowserAction(
            "read", action.url, BrowserRisk.READ, dict(action.arguments), action.description
        ))
        if result.status == "ok":
            content = str(result.data.get("visible_text") or "").strip()
            url = str(result.data.get("url") or action.url)
            if len(content) < 60:
                return BrowserActionResult(
                    "failed", "Резервный браузер открыл страницу, но не получил достаточно текста.",
                    {"url": url, "fallback": "playwright"},
                )
            event("browser_agent_read_fallback_ok", action=action.name, cause=cause[:180])
            return BrowserActionResult(
                "ok",
                f"Текст страницы {url}:\n{content[:3500]}",
                {"result": content, "url": url, "fallback": "playwright"},
            )
        return result

    def execute(self, action: BrowserAction) -> BrowserActionResult:
        supported = {"research", "agent_read", "agent_mutate", "agent_commit", "agent_financial"}
        if action.name not in supported:
            return BrowserActionResult("unsupported", "Browser Use не поддерживает этот тип browser-действия")
        expected_risks = {
            "research": BrowserRisk.READ,
            "agent_read": BrowserRisk.READ,
            "agent_mutate": BrowserRisk.MUTATE,
            "agent_commit": BrowserRisk.COMMIT,
            "agent_financial": BrowserRisk.FINANCIAL,
        }
        if action.risk is not expected_risks[action.name]:
            return BrowserActionResult("blocked", "Тип browser-действия не соответствует уровню риска")
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
            result = self._run_sync(lambda: self._run(action))
            if result.status == "failed" and action.risk is BrowserRisk.READ:
                return self._fallback_read(action, result.message)
            return result
        except Exception as exc:
            problem("browser_agent_browser_use_failed", str(exc), risk=action.risk.value, action=action.name)
            if action.risk is BrowserRisk.READ:
                return self._fallback_read(action, str(exc))
            message = str(exc)
            if os.name == "nt" and action.arguments.get("profile_namespace") == "lenta" and (
                "CDP became available" in message or "Browser process" in message
            ):
                message = (
                    "Edge/Chrome не смог открыть профиль «Ленты» для Browser Agent. "
                    "Если окно входа уже открыто, закройте только его, затем отправьте "
                    "«войти в Ленту» и повторите действие. "
                    "Данные авторизации не удаляются. Подробности: " + message[:240]
                )
            return BrowserActionResult("failed", message)


__all__ = ["BrowserUseAdapter"]
