from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import time
from typing import Callable, Iterable

from .diagnostics import event, problem
from .system_browser import ensure_system_browser


ActivityCallback = Callable[[str, dict], None]


@dataclass(frozen=True, slots=True)
class BrowserSkillDefinition:
    skill_id: str
    name: str
    start_url: str
    allowed_hosts: tuple[str, ...]
    input_hints: tuple[str, ...]
    submit_hints: tuple[str, ...]
    result_markers: tuple[str, ...] = ()
    user_action_markers: tuple[str, ...] = (
        "captcha",
        "капча",
        "подтвердите, что вы человек",
        "verify you are human",
        "проверка безопасности",
        "challenge",
    )


@dataclass(frozen=True, slots=True)
class BrowserSkillExecution:
    skill_id: str
    status: str
    page_url: str
    page_title: str
    visible_text: str
    input_value: str
    browser_name: str
    elapsed_ms: int
    message: str = ""


class BrowserSkillUnavailable(RuntimeError):
    pass


class HumanBrowserSkillRunner:
    """Декларативные browser-skills через системный Chromium-браузер.

    Леший использует уже установленный Microsoft Edge, Google Chrome или Chromium
    и хранит собственное состояние cookies/localStorage в LeshyBrain. Браузер не
    поставляется внутри дистрибутива. Если на Windows поддерживаемого браузера нет,
    Леший один раз пытается установить Edge через winget, затем Chrome как резерв.

    Никаких обходов CAPTCHA/challenge или stealth-подмены нет: если сайт требует
    участия человека, возвращается needs_user_action.
    """

    def __init__(self, *, headless: bool | None = None, timeout_ms: int = 30_000):
        if headless is None:
            raw = os.getenv("LESHY_BROWSER_HEADLESS", "1").strip().lower()
            headless = raw not in {"0", "false", "no", "off"}
        self.headless = bool(headless)
        self.timeout_ms = max(5_000, min(int(timeout_ms), 120_000))
        brain_root = Path(os.getenv("LESHY_BRAIN_DIR", "LeshyBrain")).expanduser()
        self.state_dir = brain_root / "browser"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.storage_state_path = self.state_dir / "browser-skill-storage-state.json"

    @staticmethod
    def _activity(callback: ActivityCallback | None, name: str, **payload) -> None:
        event(name, **payload)
        if callback is not None:
            try:
                callback(name, payload)
            except Exception:
                pass

    def _launch(self, playwright):
        try:
            browser = ensure_system_browser(auto_install=True)
            instance = playwright.chromium.launch(
                executable_path=browser.executable,
                headless=self.headless,
                args=["--no-first-run", "--no-default-browser-check"],
            )
            event("browser_skill_system_browser_selected", browser=browser.name, executable=browser.executable)
            return instance, browser.name
        except Exception as exc:
            raise BrowserSkillUnavailable("Не удалось запустить системный браузер Лешего: " + str(exc)) from exc

    def _new_context(self, browser):
        kwargs = {
            "locale": "ru-RU",
            "viewport": {"width": 1366, "height": 768},
        }
        if self.storage_state_path.is_file():
            kwargs["storage_state"] = str(self.storage_state_path)
        return browser.new_context(**kwargs)

    def _persist_context(self, context) -> None:
        try:
            context.storage_state(path=str(self.storage_state_path))
        except Exception as exc:
            problem("browser_skill_state_save_failed", str(exc))

    @staticmethod
    def _host_allowed(url: str, allowed_hosts: Iterable[str]) -> bool:
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower().strip(".")
        return any(host == allowed.lower().strip(".") or host.endswith("." + allowed.lower().strip(".")) for allowed in allowed_hosts)

    def _find_input(self, page, hints: tuple[str, ...]):
        for hint in hints:
            for getter in (
                lambda h=hint: page.get_by_placeholder(h, exact=False),
                lambda h=hint: page.get_by_label(h, exact=False),
            ):
                try:
                    candidate = getter()
                    if candidate.count() > 0 and candidate.first.is_visible():
                        return candidate.first
                except Exception:
                    continue

        keywords = tuple(x.lower() for x in hints) + ("track", "tracking", "трек", "отслеж")
        try:
            candidates = page.locator("input:not([type]),input[type=text],input[type=search]")
            best = None
            best_score = -1
            for index in range(min(candidates.count(), 40)):
                node = candidates.nth(index)
                if not node.is_visible():
                    continue
                attrs = " ".join(
                    str(node.get_attribute(name) or "")
                    for name in ("placeholder", "aria-label", "name", "id", "autocomplete")
                ).lower()
                score = sum(2 for word in keywords if word and word in attrs)
                if score > best_score:
                    best, best_score = node, score
            if best is not None and best_score > 0:
                return best
        except Exception:
            pass
        raise RuntimeError("Не найдено поле ввода, соответствующее browser-навыку")

    @staticmethod
    def _find_submit(page, hints: tuple[str, ...]):
        for hint in hints:
            try:
                candidate = page.get_by_role("button", name=re.compile(re.escape(hint), re.IGNORECASE))
                if candidate.count() > 0 and candidate.first.is_visible():
                    return candidate.first
            except Exception:
                pass
            try:
                candidate = page.get_by_text(re.compile(re.escape(hint), re.IGNORECASE), exact=False)
                if candidate.count() > 0 and candidate.first.is_visible():
                    return candidate.first
            except Exception:
                pass
        try:
            submits = page.locator("button,input[type=submit]")
            for index in range(min(submits.count(), 40)):
                node = submits.nth(index)
                if not node.is_visible():
                    continue
                text = (
                    node.inner_text()
                    if node.evaluate("el => el.tagName.toLowerCase() === 'button'")
                    else node.get_attribute("value") or ""
                ).lower()
                if any(word in text for word in ("отслед", "найти", "провер", "track", "search")):
                    return node
        except Exception:
            pass
        return None

    @staticmethod
    def _body_text(page) -> str:
        try:
            return "\n".join(
                line.strip()
                for line in page.locator("body").inner_text(timeout=5_000).splitlines()
                if line.strip()
            )
        except Exception:
            return ""

    def run_form_skill(
        self,
        skill: BrowserSkillDefinition,
        value: str,
        *,
        activity: ActivityCallback | None = None,
    ) -> BrowserSkillExecution:
        value = " ".join(str(value or "").split()).strip()
        if not value:
            raise ValueError("Пустое значение browser-навыка")
        started = time.monotonic()
        self._activity(activity, "browser_skill_started", skill_id=skill.skill_id, url=skill.start_url)

        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise BrowserSkillUnavailable("Playwright недоступен в этой сборке Лешего") from exc

        with sync_playwright() as pw:
            browser, browser_name = self._launch(pw)
            context = None
            try:
                context = self._new_context(browser)
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(self.timeout_ms)
                self._activity(activity, "browser_skill_opening", skill_id=skill.skill_id, status="Открываю сайт")
                page.goto(skill.start_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                if not self._host_allowed(page.url, skill.allowed_hosts):
                    raise RuntimeError(f"Сайт перенаправил browser-навык на неподтверждённый домен: {page.url}")

                before = self._body_text(page)
                lowered = before.lower()
                if any(marker.lower() in lowered for marker in skill.user_action_markers):
                    return BrowserSkillExecution(
                        skill.skill_id,
                        "needs_user_action",
                        page.url,
                        page.title(),
                        before,
                        value,
                        browser_name,
                        int((time.monotonic() - started) * 1000),
                        "Сайт требует действия пользователя (CAPTCHA/challenge).",
                    )

                field = self._find_input(page, skill.input_hints)
                self._activity(activity, "browser_skill_input", skill_id=skill.skill_id, status="Ввожу данные")
                field.fill(value)
                actual = str(field.input_value() or "").strip()
                if actual != value:
                    raise RuntimeError("Ввод в форму не подтверждён после заполнения")

                submit = self._find_submit(page, skill.submit_hints)
                self._activity(activity, "browser_skill_submit", skill_id=skill.skill_id, status="Отправляю форму")
                if submit is not None:
                    submit.click()
                else:
                    field.press("Enter")

                deadline = time.monotonic() + min(30.0, self.timeout_ms / 1000.0)
                text = ""
                while time.monotonic() < deadline:
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=1_500)
                    except Exception:
                        pass
                    text = self._body_text(page)
                    low = text.lower()
                    changed = bool(text and text != before)
                    marker_seen = any(marker.lower() in low for marker in skill.result_markers)
                    if any(marker.lower() in low for marker in skill.user_action_markers):
                        return BrowserSkillExecution(
                            skill.skill_id,
                            "needs_user_action",
                            page.url,
                            page.title(),
                            text,
                            value,
                            browser_name,
                            int((time.monotonic() - started) * 1000),
                            "Сайт требует действия пользователя (CAPTCHA/challenge).",
                        )
                    if changed and (marker_seen or value.lower() in low or page.url != skill.start_url):
                        break
                    time.sleep(0.35)

                if not text:
                    text = self._body_text(page)
                if not text or text == before:
                    raise RuntimeError("После отправки формы сайт не показал подтверждённый новый результат")
                if not self._host_allowed(page.url, skill.allowed_hosts):
                    raise RuntimeError(f"Результат browser-навыка открыл неподтверждённый домен: {page.url}")

                elapsed = int((time.monotonic() - started) * 1000)
                self._activity(
                    activity,
                    "browser_skill_verified",
                    skill_id=skill.skill_id,
                    status="Результат страницы подтверждён",
                    elapsed_ms=elapsed,
                    browser=browser_name,
                )
                return BrowserSkillExecution(
                    skill.skill_id,
                    "success",
                    page.url,
                    page.title(),
                    text,
                    value,
                    browser_name,
                    elapsed,
                )
            except BrowserSkillUnavailable:
                raise
            except Exception as exc:
                problem("browser_skill_failed", str(exc), skill_id=skill.skill_id)
                raise
            finally:
                if context is not None:
                    try:
                        self._persist_context(context)
                    except Exception:
                        pass
                    try:
                        context.close()
                    except Exception:
                        pass
                try:
                    browser.close()
                except Exception:
                    pass


__all__ = [
    "BrowserSkillDefinition",
    "BrowserSkillExecution",
    "BrowserSkillUnavailable",
    "HumanBrowserSkillRunner",
]
