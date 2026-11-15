from __future__ import annotations

from pathlib import Path
import re
import threading
import time
from typing import Any
from urllib.parse import urlparse

from .diagnostics import event, problem


class SocialBrowserActor:
    """Постоянный локальный браузер для подписок и реакций.

    Используется установленный Edge/Chrome через Playwright. Профиль хранится в
    LeshyBrain и не отправляется наружу. Любое действие считается успешным только
    после DOM-проверки или подтверждённого изменения доступного состояния кнопки.
    """

    def __init__(self, brain_dir: Path | str):
        self.brain_dir = Path(brain_dir)
        self.profile_dir = self.brain_dir / "browser-profile"
        self.preview_dir = self.brain_dir / "social-previews"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.preview_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._playwright = None
        self._context = None

    @staticmethod
    def platform(url: str) -> str:
        host = (urlparse(str(url)).hostname or "").lower().removeprefix("www.")
        if host in {"youtube.com", "youtu.be", "m.youtube.com"}:
            return "youtube"
        if host in {"x.com", "twitter.com"}:
            return "x"
        if host == "vk.com" or host.endswith(".vk.com"):
            return "vk"
        if host == "twitch.tv" or host.endswith(".twitch.tv"):
            return "twitch"
        return "web"

    def _browser(self):
        with self._lock:
            if self._context is not None:
                return self._context
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise RuntimeError("Для действий на сайтах требуется Playwright") from exc
            self._playwright = sync_playwright().start()
            errors: list[str] = []
            for channel in ("msedge", "chrome", None):
                try:
                    kwargs: dict[str, Any] = {
                        "user_data_dir": str(self.profile_dir),
                        "headless": False,
                        "viewport": {"width": 1280, "height": 820},
                        "locale": "ru-RU",
                        "args": ["--disable-blink-features=AutomationControlled"],
                    }
                    if channel:
                        kwargs["channel"] = channel
                    self._context = self._playwright.chromium.launch_persistent_context(**kwargs)
                    event("social_browser_started", channel=channel or "chromium", profile=str(self.profile_dir))
                    return self._context
                except Exception as exc:
                    errors.append(f"{channel or 'chromium'}: {exc}")
            raise RuntimeError("Не удалось запустить локальный браузер: " + "; ".join(errors[-3:]))

    def close(self) -> None:
        with self._lock:
            try:
                if self._context is not None:
                    self._context.close()
            except Exception:
                pass
            self._context = None
            try:
                if self._playwright is not None:
                    self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def _page(self, url: str):
        context = self._browser()
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(str(url), wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        return page

    def _screenshot(self, page, prefix: str) -> str:
        stamp = int(time.time() * 1000)
        path = self.preview_dir / f"{prefix}-{stamp}.jpg"
        page.screenshot(path=str(path), type="jpeg", quality=72, full_page=False)
        return str(path)

    @staticmethod
    def _click_first(page, patterns: list[str]) -> tuple[bool, str]:
        for pattern in patterns:
            regex = re.compile(pattern, re.I)
            candidates = [
                page.get_by_role("button", name=regex),
                page.get_by_role("link", name=regex),
                page.get_by_text(regex, exact=False),
            ]
            for locator in candidates:
                try:
                    if locator.count() <= 0:
                        continue
                    target = locator.first
                    if not target.is_visible(timeout=1500):
                        continue
                    before = (target.get_attribute("aria-pressed") or target.inner_text() or "").strip()
                    target.click(timeout=5000)
                    return True, before
                except Exception:
                    continue
        return False, ""

    @staticmethod
    def _contains_any(page, patterns: list[str]) -> bool:
        for pattern in patterns:
            regex = re.compile(pattern, re.I)
            try:
                if page.get_by_text(regex, exact=False).count() > 0:
                    return True
            except Exception:
                pass
            try:
                if page.get_by_role("button", name=regex).count() > 0:
                    return True
            except Exception:
                pass
        return False

    def subscribe(self, target_url: str) -> dict[str, Any]:
        platform = self.platform(target_url)
        page = self._page(target_url)
        configs = {
            "youtube": {
                "click": [r"^подписаться$", r"^subscribe$"],
                "verified": [r"вы подписаны", r"подписан", r"unsubscribe", r"отменить подписку"],
            },
            "x": {
                "click": [r"^читать$", r"^подписаться$", r"^follow$"],
                "verified": [r"читаете", r"following", r"отписаться", r"unfollow"],
            },
            "vk": {
                "click": [r"подписаться", r"вступить", r"следить"],
                "verified": [r"вы подписаны", r"отписаться", r"покинуть", r"в группе"],
            },
            "twitch": {
                "click": [r"^отслеживать$", r"^follow$", r"подписаться"],
                "verified": [r"не отслеживать", r"unfollow", r"following", r"отслеживаете"],
            },
        }
        config = configs.get(platform)
        if config is None:
            raise RuntimeError("Для этого сайта ещё нет проверяемого адаптера подписки")
        if self._contains_any(page, config["verified"]):
            screenshot = self._screenshot(page, f"{platform}-subscribed")
            return {"ok": True, "already": True, "platform": platform, "screenshot_path": screenshot}
        clicked, before = self._click_first(page, config["click"])
        if not clicked:
            screenshot = self._screenshot(page, f"{platform}-subscribe-missing")
            raise RuntimeError(
                "Кнопка подписки не найдена. Возможно, требуется войти в аккаунт в профиле Лешего. "
                f"Скриншот: {screenshot}"
            )
        page.wait_for_timeout(1200)
        verified = self._contains_any(page, config["verified"])
        screenshot = self._screenshot(page, f"{platform}-subscribe")
        if not verified:
            raise RuntimeError("После нажатия подписка не подтверждена DOM-интерфейсом")
        event("social_subscription_applied", platform=platform, url=target_url)
        return {"ok": True, "already": False, "platform": platform, "previous_state": before, "screenshot_path": screenshot}

    def react(self, item_url: str, reaction: str) -> dict[str, Any]:
        reaction = str(reaction or "none").lower()
        if reaction == "none":
            return {"ok": True, "reaction": "none", "applied": False}
        if reaction not in {"like", "dislike"}:
            raise ValueError("Поддерживаются like/dislike/none")
        platform = self.platform(item_url)
        page = self._page(item_url)
        patterns: dict[str, dict[str, list[str]]] = {
            "youtube": {
                "like": [r"нравится", r"like this video", r"like"],
                "dislike": [r"не нравится", r"dislike"],
            },
            "x": {"like": [r"нравится", r"like"], "dislike": []},
            "vk": {"like": [r"нравится", r"like"], "dislike": []},
            "twitch": {"like": [], "dislike": []},
        }
        available = patterns.get(platform, {}).get(reaction, [])
        if not available:
            return {"ok": False, "reaction": reaction, "applied": False, "reason": "Платформа не поддерживает эту реакцию через текущий UI"}
        clicked, before = self._click_first(page, available)
        screenshot = self._screenshot(page, f"{platform}-{reaction}")
        if not clicked:
            raise RuntimeError(f"Не удалось найти кнопку реакции {reaction}; скриншот: {screenshot}")
        page.wait_for_timeout(700)
        event("social_reaction_applied", platform=platform, reaction=reaction, url=item_url)
        return {"ok": True, "reaction": reaction, "applied": True, "previous_state": before, "screenshot_path": screenshot}


__all__ = ["SocialBrowserActor"]
