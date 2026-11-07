from __future__ import annotations

from pathlib import Path
import os
import time
from typing import Any, Iterable
from urllib.parse import urlparse

from .browser_agent import BrowserAction, BrowserActionResult
from .diagnostics import event, problem
from .browser_launch_policy import chromium_launch_policy
from .system_browser import ensure_system_browser


_CAPTCHA_MARKERS = (
    "captcha",
    "капча",
    "verify you are human",
    "подтвердите, что вы человек",
    "проверка безопасности",
    "cloudflare challenge",
)


class PlaywrightBrowserAdapter:
    """Детерминированный Browser Agent поверх системного Chromium-браузера."""

    adapter_id = "playwright"

    def __init__(self, *, headless: bool | None = None, timeout_ms: int = 30_000):
        policy = chromium_launch_policy()
        self.headless = policy.headless if headless is None else (bool(headless) if policy.can_open_window else True)
        self.timeout_ms = max(5_000, min(int(timeout_ms), 120_000))
        root = Path(os.getenv("LESHY_BRAIN_DIR", "LeshyBrain")).expanduser()
        self.state_dir = root / "browser"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.state_dir.chmod(0o700)
        except Exception:
            pass
        self.storage_state_path = self.state_dir / "browser-agent-storage-state.json"

    @staticmethod
    def available() -> bool:
        try:
            import playwright.sync_api  # noqa: F401
            return True
        except Exception:
            return False

    @staticmethod
    def _host(url: str) -> str:
        return (urlparse(str(url or "")).hostname or "").lower().strip(".")

    @classmethod
    def _host_allowed(cls, url: str, allowed_hosts: Iterable[str]) -> bool:
        host = cls._host(url)
        if not host:
            return False
        normalized = tuple(str(x).lower().strip(".") for x in allowed_hosts if str(x).strip())
        return any(host == item or host.endswith("." + item) for item in normalized)

    @classmethod
    def _allowed_hosts(cls, action: BrowserAction) -> tuple[str, ...]:
        explicit = action.arguments.get("allowed_hosts") or ()
        if isinstance(explicit, str):
            explicit = [explicit]
        hosts = tuple(dict.fromkeys(str(x).lower().strip().strip(".") for x in explicit if str(x).strip()))
        if hosts:
            return hosts
        primary = cls._host(action.url)
        return (primary,) if primary else ()

    def _launch(self, pw):
        system_browser = ensure_system_browser(auto_install=True)
        policy = chromium_launch_policy()
        browser = pw.chromium.launch(
            executable_path=system_browser.executable,
            headless=self.headless,
            chromium_sandbox=policy.chromium_sandbox,
            args=["--no-first-run", "--no-default-browser-check", *policy.args],
        )
        event("browser_agent_playwright_started", browser=system_browser.name, executable=system_browser.executable)
        return browser, system_browser.name

    def _new_context(self, browser):
        kwargs: dict[str, Any] = {"locale": "ru-RU", "viewport": {"width": 1366, "height": 768}}
        if self.storage_state_path.is_file():
            kwargs["storage_state"] = str(self.storage_state_path)
        return browser.new_context(**kwargs)

    def _persist(self, context) -> None:
        try:
            context.storage_state(path=str(self.storage_state_path))
            try:
                self.storage_state_path.chmod(0o600)
            except Exception:
                pass
        except Exception as exc:
            problem("browser_agent_state_save_failed", str(exc))

    @staticmethod
    def _visible_text(page, *, limit: int = 20_000) -> str:
        try:
            text = page.locator("body").inner_text(timeout=5_000)
        except Exception:
            return ""
        compact = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        return compact[: max(1_000, int(limit))]

    @staticmethod
    def _needs_user(text: str) -> bool:
        low = str(text or "").lower()
        return any(marker in low for marker in _CAPTCHA_MARKERS)

    @staticmethod
    def _locator(page, step: dict[str, Any]):
        selector = str(step.get("selector") or "").strip()
        if selector:
            return page.locator(selector).first
        role = str(step.get("role") or "").strip()
        name = str(step.get("name") or "").strip()
        if role and name:
            return page.get_by_role(role, name=name, exact=False).first
        label = str(step.get("label") or "").strip()
        if label:
            return page.get_by_label(label, exact=False).first
        placeholder = str(step.get("placeholder") or "").strip()
        if placeholder:
            return page.get_by_placeholder(placeholder, exact=False).first
        text = str(step.get("text") or "").strip()
        if text:
            return page.get_by_text(text, exact=False).first
        raise ValueError("Шаг браузера требует selector либо семантический locator")

    def _goto(self, page, url: str, allowed_hosts: tuple[str, ...]) -> None:
        if not self._host_allowed(url, allowed_hosts):
            raise PermissionError(f"Переход на неподтверждённый домен заблокирован: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        if not self._host_allowed(page.url, allowed_hosts):
            raise PermissionError(f"Перенаправление на неподтверждённый домен заблокировано: {page.url}")

    def _run_step(self, page, step: dict[str, Any], allowed_hosts: tuple[str, ...]) -> None:
        kind = str(step.get("kind") or "").strip().lower()
        if kind == "goto":
            self._goto(page, str(step.get("url") or ""), allowed_hosts)
            return
        if kind == "fill":
            locator = self._locator(page, step)
            value = str(step.get("value") or "")
            locator.fill(value)
            if str(locator.input_value() or "") != value:
                raise RuntimeError("Браузер не подтвердил заполнение поля")
            return
        if kind == "click":
            self._locator(page, step).click(timeout=self.timeout_ms)
            return
        if kind == "press":
            key = str(step.get("key") or "").strip()
            if not key:
                raise ValueError("Шаг press требует key")
            self._locator(page, step).press(key)
            return
        if kind == "wait":
            page.wait_for_timeout(max(0, min(int(step.get("milliseconds", 500)), 10_000)))
            return
        if kind == "wait_for":
            self._locator(page, step).wait_for(state=str(step.get("state") or "visible"), timeout=self.timeout_ms)
            return
        raise ValueError(f"Неподдерживаемый browser-шаг: {kind}")

    def execute(self, action: BrowserAction) -> BrowserActionResult:
        if action.name not in {"open", "read", "extract", "workflow"}:
            return BrowserActionResult("unsupported", f"Playwright не поддерживает действие {action.name}")
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return BrowserActionResult("unavailable", "Playwright недоступен")

        allowed_hosts = self._allowed_hosts(action)
        if not allowed_hosts or not self._host_allowed(action.url, allowed_hosts):
            return BrowserActionResult("blocked", "Стартовый URL не разрешён политикой Browser Agent")

        started = time.monotonic()
        with sync_playwright() as pw:
            browser = None
            context = None
            try:
                browser, browser_name = self._launch(pw)
                context = self._new_context(browser)
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(self.timeout_ms)
                self._goto(page, action.url, allowed_hosts)
                initial_text = self._visible_text(page)
                if self._needs_user(initial_text):
                    return BrowserActionResult(
                        "needs_user_action",
                        "Сайт требует участия пользователя (CAPTCHA/challenge).",
                        {"url": page.url, "title": page.title(), "visible_text": initial_text},
                    )

                if action.name == "workflow":
                    steps = action.arguments.get("steps") or []
                    if not isinstance(steps, list):
                        raise ValueError("workflow.steps должен быть списком")
                    if len(steps) > 64:
                        raise ValueError("workflow содержит слишком много шагов")
                    for index, raw_step in enumerate(steps):
                        if not isinstance(raw_step, dict):
                            raise ValueError(f"Шаг {index + 1} должен быть объектом")
                        self._run_step(page, dict(raw_step), allowed_hosts)
                        if not self._host_allowed(page.url, allowed_hosts):
                            raise PermissionError(f"После шага {index + 1} открыт неподтверждённый домен: {page.url}")
                        visible = self._visible_text(page)
                        if self._needs_user(visible):
                            return BrowserActionResult(
                                "needs_user_action",
                                "Сайт требует участия пользователя (CAPTCHA/challenge).",
                                {"step": index + 1, "url": page.url, "title": page.title(), "visible_text": visible},
                            )

                try:
                    page.wait_for_load_state("domcontentloaded", timeout=min(self.timeout_ms, 5_000))
                except Exception:
                    pass
                visible = self._visible_text(page, limit=int(action.arguments.get("text_limit", 20_000)))
                elapsed_ms = int((time.monotonic() - started) * 1000)
                event("browser_agent_playwright_completed", action=action.name, url=page.url, elapsed_ms=elapsed_ms)
                return BrowserActionResult(
                    "ok",
                    "Браузерное действие выполнено и состояние страницы прочитано.",
                    {"url": page.url, "title": page.title(), "visible_text": visible, "browser": browser_name, "elapsed_ms": elapsed_ms},
                )
            except PermissionError as exc:
                event("browser_agent_playwright_blocked", action=action.name, error=str(exc))
                return BrowserActionResult("blocked", str(exc))
            except Exception as exc:
                problem("browser_agent_playwright_failed", str(exc), action=action.name)
                return BrowserActionResult("failed", str(exc))
            finally:
                if context is not None:
                    self._persist(context)
                    try:
                        context.close()
                    except Exception:
                        pass
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:
                        pass


__all__ = ["PlaywrightBrowserAdapter"]
