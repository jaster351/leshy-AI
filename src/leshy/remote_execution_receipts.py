"""Conservative classification of remote terminal execution outcomes.

This module never executes commands and never grants remote permissions.
An unknown outcome must not be retried automatically: the remote process
may have completed before the connection was lost.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Outcome(str, Enum):
    NOT_STARTED = "not_started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TerminalReceipt:
    request_id: str
    started: bool
    exit_code: Optional[int] = None
    transport_lost: bool = False
    cancelled: bool = False

    def __post_init__(self) -> None:
        if not self.request_id or not self.request_id.strip():
            raise ValueError("request_id must be nonempty")
        if self.exit_code is not None and (isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int)):
            raise TypeError("exit_code must be an integer or None")
        if not self.started and self.exit_code is not None:
            raise ValueError("a process that never started cannot have an exit code")

    @property
    def outcome(self) -> Outcome:
        if self.transport_lost or self.cancelled:
            return Outcome.UNKNOWN
        if not self.started:
            return Outcome.NOT_STARTED
        if self.exit_code is not None:
            return Outcome.SUCCEEDED if self.exit_code == 0 else Outcome.FAILED
        return Outcome.UNKNOWN

    @property
    def automatic_retry_safe(self) -> bool:
        # Cancellation, transport loss or missing acknowledgement can mask
        # a completed side effect. Only a confirmed non-start is retryable.
        return not self.started and not self.cancelled and not self.transport_lost
