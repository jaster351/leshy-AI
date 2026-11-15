from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import os
import time
from typing import Any, Iterable
from urllib.parse import urlparse

from .diagnostics import event, problem
from .modules import ModuleManifest, ModuleRequest, ModuleResponse
from .system_browser import ensure_system_browser


_INTERACTIVE_SELECTOR = (
    "a,button,input,textarea,select,summary,[role=button],[role=link],"
    "[role=checkbox],[role=radio],[role=combobox],[contenteditable=true]"
)

_USER_ACTION_MARKERS = (
    "captcha",
    "verify you are human",
    "подтвердите, что вы человек",
    "проверка безопасности",
    "security challenge",
    "cloudflare challenge",
)


@dataclass(frozen=True, slots=True)
class BrowserElement:
    element_id: str
    tag: str
    role: str
    text: str
    label: str
    placeholder: str
    name: str
    element_type: str
    href: str
    value: str
    disabled: bool
    checked: bool | None
    selector: str
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class BrowserSnapshot:
    status: str
    url: str
    title: str
    browser_name: str
    user_agent: str
    visible_text: str
    elements: tuple[BrowserElement, ...]
    cookies_count: int
    screenshot_path: str
    elapsed_ms: int
    message: str = ""


@dataclass(frozen=True, slots=True)
class BrowserAction:
    kind: str
    selector: str = ""
    value: str = ""
    wait_ms: int = 0


class HumanSystemBrowser:
    """Системный браузер с отдельным постоянным профилем Лешего.

    Используется уже установленный Microsoft Edge, Google Chrome или Chromium.
    Сам браузер не входит в дистрибутив Лешего. Если на Windows поддерживаемого
    браузера нет, Леший пытается установить Edge через winget, затем Chrome.

    Страница рендерится реальным системным браузером, а Леший получает видимый
    текст, screenshot viewport и нумерованную карту видимых интерактивных элементов.
    CAPTCHA/challenge не обходятся: модуль возвращает needs_user_action.
    """

    def __init__(
        self,
        profile_dir: Path | None = None,
        screenshots_dir: Path | None = None,
        *,
        headless: bool | None = None,
        timeout_ms: int = 30_000,
    ) -> None:
        brain_root = Path(os.getenv("LESHY_BRAIN_DIR", "LeshyBrain")).expanduser()
        self.profile_dir = Path(profile_dir or brain_root / "browser" / "system-profile")
        self.screenshots_dir = Path(screenshots_dir or brain_root / "browser" / "screenshots")
        if headless is None:
            raw = os.getenv("LESHY_HUMAN_BROWSER_HEADLESS", "0").strip().lower()
            headless = raw in {"1", "true", "yes", "on"}
        self.headless = bool(headless)
        self.timeout_ms = max(5_000, min(int(timeout_ms), 120_000))
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def validate_url(url: str) -> str:
        value = str(url or "").strip()
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Human Browser разрешает только обычные http/https URL")
        return value

    @staticmethod
    def normalize_actions(actions: Iterable[BrowserAction | dict[str, Any]] | None) -> tuple[BrowserAction, ...]:
        result: list[BrowserAction] = []
        for raw in actions or ():
            action = raw if isinstance(raw, BrowserAction) else BrowserAction(
                kind=str(raw.get("kind") or "").strip().lower(),
                selector=str(raw.get("selector") or "").strip(),
                value=str(raw.get("value") or ""),
                wait_ms=int(raw.get("wait_ms") or 0),
            )
            if action.kind not in {"click", "fill", "press", "wait"}:
                raise ValueError(f"Неизвестное действие Human Browser: {action.kind}")
            if action.kind in {"click", "fill", "press"} and not action.selector:
                raise ValueError(f"Для действия {action.kind} нужен selector из карты элементов")
            result.append(action)
        return tuple(result)

    @staticmethod
    def _needs_user_action(text: str) -> bool:
        value = str(text or "").lower()
        return any(marker in value for marker in _USER_ACTION_MARKERS)

    @staticmethod
    def _visible_text(page) -> str:
        try:
            text = page.locator("body").inner_text(timeout=5_000)
            return "\n".join(line.strip() for line in text.splitlines() if line.strip())[:40_000]
        except Exception:
            return ""

    @staticmethod
    def _element_map(page) -> tuple[BrowserElement, ...]:
        script = r"""
(nodes) => {
  const esc = (s) => {
    if (window.CSS && CSS.escape) return CSS.escape(String(s));
    return String(s).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
  };
  const unique = (selector) => {
    try { return document.querySelectorAll(selector).length === 1; }
    catch (_) { return false; }
  };
  const selectorFor = (el) => {
    if (el.id) return '#' + esc(el.id);
    const name = el.getAttribute('name');
    if (name) {
      const s = el.tagName.toLowerCase() + '[name="' + String(name).replace(/"/g, '\\"') + '"]';
      if (unique(s)) return s;
    }
    const aria = el.getAttribute('aria-label');
    if (aria) {
      const s = el.tagName.toLowerCase() + '[aria-label="' + String(aria).replace(/"/g, '\\"') + '"]';
      if (unique(s)) return s;
    }
    const parts = [];
    let node = el;
    while (node && node.nodeType === Node.ELEMENT_NODE && parts.length < 5) {
      let part = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(x => x.tagName === node.tagName);
        if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(node) + 1) + ')';
      }
      parts.unshift(part);
      const candidate = parts.join(' > ');
      if (unique(candidate)) return candidate;
      node = parent;
    }
    return parts.join(' > ');
  };
  const labelFor = (el) => {
    if (el.labels && el.labels.length) return Array.from(el.labels).map(x => x.innerText || x.textContent || '').join(' ').trim();
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      return labelledBy.split(/\s+/).map(id => (document.getElementById(id)?.innerText || '')).join(' ').trim();
    }
    return (el.getAttribute('aria-label') || '').trim();
  };
  const out = [];
  for (const el of nodes) {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    const visible = rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && Number(style.opacity || 1) > 0;
    if (!visible) continue;
    out.push({
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || '',
      text: ((el.innerText || el.textContent || '') + '').trim().replace(/\s+/g, ' ').slice(0, 300),
      label: labelFor(el).replace(/\s+/g, ' ').slice(0, 300),
      placeholder: (el.getAttribute('placeholder') || '').slice(0, 300),
      name: (el.getAttribute('name') || '').slice(0, 200),
      element_type: (el.getAttribute('type') || '').slice(0, 80),
      href: (el.href || '').slice(0, 1000),
      value: ('value' in el ? String(el.value || '') : '').slice(0, 1000),
      disabled: !!el.disabled,
      checked: ('checked' in el ? !!el.checked : null),
      selector: selectorFor(el),
      x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height)
    });
    if (out.length >= 160) break;
  }
  return out;
}
"""
        rows = page.locator(_INTERACTIVE_SELECTOR).evaluate_all(script)
        return tuple(BrowserElement(element_id=f"e{index + 1}", **row) for index, row in enumerate(rows))

    def _snapshot(
        self,
        page,
        context,
        *,
        browser_name: str,
        started: float,
        status: str = "success",
        message: str = "",
    ) -> BrowserSnapshot:
        visible_text = self._visible_text(page)
        if status == "success" and self._needs_user_action(visible_text):
            status = "needs_user_action"
            message = "Сайт требует действия пользователя (CAPTCHA/challenge)."
        try:
            elements = self._element_map(page)
        except Exception as exc:
            problem("human_browser_element_map_failed", str(exc), url=page.url)
            elements = ()
        try:
            user_agent = str(page.evaluate("navigator.userAgent") or "")
        except Exception:
            user_agent = ""
        try:
            cookies_count = len(context.cookies())
        except Exception:
            cookies_count = 0
        stamp = time.strftime("%Y%m%d-%H%M%S")
        screenshot_path = self.screenshots_dir / f"viewport-{stamp}-{int(time.time() * 1000) % 1000:03d}.png"
        try:
            page.screenshot(path=str(screenshot_path), full_page=False)
            screenshot_value = str(screenshot_path)
        except Exception as exc:
            problem("human_browser_screenshot_failed", str(exc), url=page.url)
            screenshot_value = ""
        return BrowserSnapshot(
            status=status,
            url=str(page.url or ""),
            title=str(page.title() or ""),
            browser_name=browser_name,
            user_agent=user_agent,
            visible_text=visible_text,
            elements=elements,
            cookies_count=cookies_count,
            screenshot_path=screenshot_value,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            message=message,
        )

    def inspect(self, url: str) -> BrowserSnapshot:
        return self.run(url, actions=())

    def run(self, url: str, *, actions: Iterable[BrowserAction | dict[str, Any]] | None = None) -> BrowserSnapshot:
        target = self.validate_url(url)
        plan = self.normalize_actions(actions)
        started = time.monotonic()
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise RuntimeError("Playwright недоступен в этой сборке Лешего") from exc

        with sync_playwright() as pw:
            context = None
            try:
                browser = ensure_system_browser(auto_install=True)
                event("human_browser_started", url=target, browser=browser.name, headless=self.headless)
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    executable_path=browser.executable,
                    headless=self.headless,
                    locale="ru-RU",
                    viewport={"width": 1366, "height": 768},
                    args=["--no-first-run", "--no-default-browser-check"],
                )
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(self.timeout_ms)
                page.goto(target, wait_until="domcontentloaded", timeout=self.timeout_ms)

                first_text = self._visible_text(page)
                if self._needs_user_action(first_text):
                    return self._snapshot(
                        page,
                        context,
                        browser_name=browser.name,
                        started=started,
                        status="needs_user_action",
                        message="Сайт требует действия пользователя (CAPTCHA/challenge).",
                    )

                for action in plan:
                    if action.kind == "wait":
                        time.sleep(max(0, min(action.wait_ms, 120_000)) / 1000.0)
                        continue
                    locator = page.locator(action.selector).first
                    if action.kind == "click":
                        locator.click()
                    elif action.kind == "fill":
                        locator.fill(action.value)
                    elif action.kind == "press":
                        locator.press(action.value or "Enter")
                    if action.wait_ms:
                        time.sleep(max(0, min(action.wait_ms, 120_000)) / 1000.0)
                    text = self._visible_text(page)
                    if self._needs_user_action(text):
                        return self._snapshot(
                            page,
                            context,
                            browser_name=browser.name,
                            started=started,
                            status="needs_user_action",
                            message="Сайт потребовал действие пользователя после выполнения шага.",
                        )

                result = self._snapshot(page, context, browser_name=browser.name, started=started)
                event(
                    "human_browser_finished",
                    url=result.url,
                    browser=browser.name,
                    status=result.status,
                    elements=len(result.elements),
                    cookies=result.cookies_count,
                    elapsed_ms=result.elapsed_ms,
                )
                return result
            except Exception as exc:
                problem("human_browser_failed", str(exc), url=target)
                raise
            finally:
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        pass


# Совместимость с ранним именем класса 1.10.4/1.10.5. Это только Python alias;
# Firefox больше не используется и не поставляется в дистрибутиве.
HumanFirefoxBrowser = HumanSystemBrowser


@dataclass(slots=True)
class HumanBrowserModule:
    """Отдельный модуль браузера, пригодный для вызова другими навыками Лешего."""

    browser: HumanSystemBrowser = field(default_factory=HumanSystemBrowser)
    manifest: ModuleManifest = field(
        default_factory=lambda: ModuleManifest(
            name="human-browser",
            version="0.2.0",
            description="Системный Edge/Chrome/Chromium с отдельным постоянным профилем и картой видимой страницы",
            priority=700,
        )
    )

    def can_handle(self, request: ModuleRequest) -> bool:
        event_name = str(request.metadata.get("event") or "")
        return event_name in {"human_browser_inspect", "human_browser_run"}

    def handle(self, request: ModuleRequest) -> ModuleResponse:
        url = str(request.metadata.get("url") or request.text or "").strip()
        actions = request.metadata.get("actions") or ()
        snapshot = self.browser.run(url, actions=actions)
        return ModuleResponse(
            handled=True,
            text=snapshot.visible_text,
            metadata={
                "status": snapshot.status,
                "browser": snapshot.browser_name,
                "user_agent": snapshot.user_agent,
                "url": snapshot.url,
                "title": snapshot.title,
                "cookies_count": snapshot.cookies_count,
                "screenshot_path": snapshot.screenshot_path,
                "elapsed_ms": snapshot.elapsed_ms,
                "message": snapshot.message,
                "elements": [asdict(item) for item in snapshot.elements],
            },
        )


def create_human_browser_module() -> HumanBrowserModule:
    return HumanBrowserModule()


__all__ = [
    "BrowserAction",
    "BrowserElement",
    "BrowserSnapshot",
    "HumanFirefoxBrowser",
    "HumanSystemBrowser",
    "HumanBrowserModule",
    "create_human_browser_module",
]
