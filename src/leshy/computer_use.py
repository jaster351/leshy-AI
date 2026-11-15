from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from time import monotonic
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class ScreenFrame:
    """Ephemeral screen snapshot.

    `image` is deliberately kept in memory. Implementations must not persist it
    unless the caller explicitly enabled screenshot recording for the task.
    """

    image: bytes
    width: int
    height: int
    monitor_id: str = "primary"
    active_window: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        return sha256(self.image).hexdigest()


@dataclass(frozen=True)
class ScreenUnderstanding:
    summary: str
    visible_text: str = ""
    active_window: str = ""
    elements: tuple[dict[str, Any], ...] = ()
    confidence: float = 0.0


@dataclass(frozen=True)
class ComputerAction:
    kind: str
    arguments: dict[str, Any]
    description: str
    risk: str = "low"
    expected_change: str = ""


@dataclass(frozen=True)
class ActionResult:
    action: ComputerAction
    before_fingerprint: str
    after_fingerprint: str
    changed: bool
    verified: bool
    verification: str
    duration_ms: int


class ScreenObserver(Protocol):
    def capture(self) -> ScreenFrame: ...


class ScreenAnalyzer(Protocol):
    def analyze(self, frame: ScreenFrame) -> ScreenUnderstanding: ...


class ComputerExecutor(Protocol):
    def execute(self, action: ComputerAction) -> None: ...


class ComputerUseController:
    """Universal local observe -> act -> observe -> verify loop.

    Platform-specific capture, accessibility/vision analysis and input injection
    live behind protocols. This core intentionally refuses blind actions: a fresh
    frame is always captured before an action and another one afterwards.
    """

    def __init__(
        self,
        observer: ScreenObserver,
        analyzer: ScreenAnalyzer,
        executor: ComputerExecutor,
        *,
        approval_required: Callable[[ComputerAction], bool] | None = None,
        approval: Callable[[ComputerAction], bool] | None = None,
        activity: Callable[[str, dict[str, Any]], None] | None = None,
        permission_check: Callable[[], bool] | None = None,
    ):
        self.observer = observer
        self.analyzer = analyzer
        self.executor = executor
        self.approval_required = approval_required or (lambda action: action.risk in {"high", "critical"})
        self.approval = approval or (lambda action: False)
        self.activity = activity or (lambda name, payload: None)
        # Fail closed: без явного разрешения нельзя управлять рабочим столом.
        self.permission_check = permission_check or (lambda: False)
        self.enabled = False

    def _require_enabled(self) -> None:
        if not self.enabled or not self.permission_check():
            self.enabled = False
            raise RuntimeError("Computer Use выключен или разрешение ОС отозвано")

    def start(self) -> None:
        if not self.permission_check():
            raise PermissionError("Управление ОС выключено")
        self.enabled = True
        self.activity("computer.started", {})

    def stop(self) -> None:
        self.enabled = False
        self.activity("computer.stopped", {})

    def observe(self) -> tuple[ScreenFrame, ScreenUnderstanding]:
        self._require_enabled()
        self.activity("computer.observing", {"status": "Смотрю экран"})
        frame = self.observer.capture()
        understanding = self.analyzer.analyze(frame)
        self.activity(
            "computer.observed",
            {
                "status": "Экран распознан",
                "active_window": understanding.active_window or frame.active_window,
                "summary": understanding.summary,
                "confidence": understanding.confidence,
            },
        )
        return frame, understanding

    def act(
        self,
        action: ComputerAction,
        *,
        verify: Callable[[ScreenUnderstanding, ScreenUnderstanding, ComputerAction], tuple[bool, str]] | None = None,
    ) -> ActionResult:
        self._require_enabled()

        if self.approval_required(action):
            self.activity(
                "computer.approval_required",
                {"status": "Нужно подтверждение", "action": action.description, "risk": action.risk},
            )
            if not self.approval(action):
                raise PermissionError("Действие не выполнено без подтверждения пользователя")

        before_frame, before = self.observe()
        self.activity(
            "computer.action",
            {
                "status": action.description,
                "kind": action.kind,
                "expected_change": action.expected_change,
            },
        )

        self._require_enabled()
        started = monotonic()
        self.executor.execute(action)
        self._require_enabled()
        after_frame, after = self.observe()
        elapsed_ms = int((monotonic() - started) * 1000)
        changed = before_frame.fingerprint != after_frame.fingerprint

        if verify is not None:
            verified, verification = verify(before, after, action)
        else:
            verified = changed
            verification = (
                "Состояние экрана изменилось после действия"
                if changed
                else "Ожидаемое изменение экрана не подтверждено"
            )

        self.activity(
            "computer.verified" if verified else "computer.verification_failed",
            {
                "status": "Готово" if verified else "Не удалось — проверяю другой способ",
                "action": action.description,
                "changed": changed,
                "verification": verification,
            },
        )
        return ActionResult(
            action=action,
            before_fingerprint=before_frame.fingerprint,
            after_fingerprint=after_frame.fingerprint,
            changed=changed,
            verified=bool(verified),
            verification=str(verification),
            duration_ms=elapsed_ms,
        )
