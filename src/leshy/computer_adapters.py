from __future__ import annotations

import platform
from typing import Any

from .computer_use import ComputerAction, ScreenFrame


class MssScreenObserver:
    """Cross-platform local screen capture using the optional `mss` package."""

    def __init__(self, monitor: int = 1):
        self.monitor = max(0, int(monitor))

    @staticmethod
    def available() -> bool:
        try:
            import mss  # noqa: F401
            return True
        except Exception:
            return False

    def capture(self) -> ScreenFrame:
        try:
            import mss
        except ImportError as exc:
            raise RuntimeError(
                "Для локального захвата экрана требуется пакет mss; облачный захват запрещён"
            ) from exc

        with mss.mss() as sct:
            monitors = sct.monitors
            if not monitors:
                raise RuntimeError("Операционная система не вернула доступные экраны")
            index = self.monitor if self.monitor < len(monitors) else 1
            monitor = monitors[index]
            shot = sct.grab(monitor)
            # BGRA bytes are passed directly to the local analyzer. No file is
            # created and no network request is made.
            return ScreenFrame(
                image=bytes(shot.bgra),
                width=int(shot.width),
                height=int(shot.height),
                monitor_id=str(index),
                metadata={
                    "pixel_format": "BGRA",
                    "left": int(monitor.get("left", 0)),
                    "top": int(monitor.get("top", 0)),
                    "platform": platform.system().lower(),
                },
            )


class PyAutoGuiExecutor:
    """Cross-platform local keyboard/mouse executor.

    Higher-level DOM/accessibility adapters should be preferred where available;
    this executor is the universal visual fallback.
    """

    def __init__(self, pause: float = 0.05):
        self.pause = max(0.0, float(pause))

    @staticmethod
    def available() -> bool:
        try:
            import pyautogui  # noqa: F401
            return True
        except Exception:
            return False

    @staticmethod
    def _module():
        try:
            import pyautogui
        except ImportError as exc:
            raise RuntimeError(
                "Для локального управления мышью/клавиатурой требуется pyautogui"
            ) from exc
        pyautogui.FAILSAFE = True
        return pyautogui

    def execute(self, action: ComputerAction) -> None:
        pg = self._module()
        pg.PAUSE = self.pause
        kind = str(action.kind).strip().lower()
        args: dict[str, Any] = dict(action.arguments or {})

        if kind == "click":
            pg.click(int(args["x"]), int(args["y"]), clicks=int(args.get("clicks", 1)), button=str(args.get("button", "left")))
            return
        if kind == "move":
            pg.moveTo(int(args["x"]), int(args["y"]), duration=float(args.get("duration", 0.1)))
            return
        if kind == "scroll":
            pg.scroll(int(args.get("clicks", args.get("amount", 0))))
            return
        if kind == "write":
            pg.write(str(args.get("text", "")), interval=float(args.get("interval", 0.01)))
            return
        if kind == "press":
            pg.press(str(args["key"]))
            return
        if kind == "hotkey":
            keys = args.get("keys") or []
            if not isinstance(keys, (list, tuple)) or not keys:
                raise ValueError("hotkey требует непустой список keys")
            pg.hotkey(*(str(key) for key in keys))
            return
        raise ValueError(f"Неподдерживаемое компьютерное действие: {action.kind}")


class BrowserHotkeys:
    """Portable browser-tab actions for the visual fallback executor."""

    @staticmethod
    def modifier() -> str:
        return "command" if platform.system().lower() == "darwin" else "ctrl"

    @classmethod
    def new_tab(cls) -> ComputerAction:
        return ComputerAction("hotkey", {"keys": [cls.modifier(), "t"]}, "Открываю новую вкладку", expected_change="Должна появиться новая вкладка")

    @classmethod
    def close_tab(cls) -> ComputerAction:
        return ComputerAction("hotkey", {"keys": [cls.modifier(), "w"]}, "Закрываю текущую вкладку", expected_change="Текущая вкладка должна закрыться")

    @classmethod
    def next_tab(cls) -> ComputerAction:
        return ComputerAction("hotkey", {"keys": [cls.modifier(), "tab"]}, "Переключаю вкладку", expected_change="Активная вкладка должна измениться")

    @classmethod
    def focus_address_bar(cls) -> ComputerAction:
        return ComputerAction("hotkey", {"keys": [cls.modifier(), "l"]}, "Перехожу в адресную строку", expected_change="Фокус должен оказаться в адресной строке")
