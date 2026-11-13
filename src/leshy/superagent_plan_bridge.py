"""Owner-bound durable plans attached to real SuperAgent tickets.

This is an explicit coordinator API: plans are stored and can be claimed by a
trusted executor, but creating a plan never starts an unapproved tool action.
"""
from __future__ import annotations

from typing import Any
import sqlite3

from .durable_plan_store import DurablePlanStore, PlanError


_TERMINAL_TICKETS = frozenset({"stopped", "ready", "failed", "blocked"})


class SuperAgentPlanBridge:
    def __init__(self, service: Any):
        self.service = service
        self.store = DurablePlanStore(service.storage.path)

    def _ticket(self, ticket_id: str, owner_id: str) -> dict[str, Any]:
        if not isinstance(ticket_id, str) or not ticket_id.startswith("SA-"):
            raise PlanError("A valid SuperAgent ticket ID is required")
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise PlanError("Owner is required")
        # The ticket service is authoritative for owner identity and lifecycle.
        return self.service.get(ticket_id, owner_id=owner_id)

    def create(self, ticket_id: str, owner_id: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
        ticket = self._ticket(ticket_id, owner_id)
        if ticket["state"] in _TERMINAL_TICKETS:
            raise PlanError("Cannot attach a plan to a terminal ticket")
        try:
            plan_id = self.store.create(owner_id, steps, plan_id=ticket_id)
        except sqlite3.IntegrityError as exc:
            raise PlanError("A plan already exists for this ticket") from exc
        return self.store.snapshot(plan_id, owner_id)

    def snapshot(self, ticket_id: str, owner_id: str) -> dict[str, Any]:
        self._ticket(ticket_id, owner_id)
        return self.store.snapshot(ticket_id, owner_id)

    def control(self, ticket_id: str, owner_id: str, action: str) -> dict[str, Any]:
        ticket = self._ticket(ticket_id, owner_id)
        if action == "resume" and ticket["state"] in _TERMINAL_TICKETS:
            raise PlanError("Cannot resume a plan of a terminal ticket")
        self.store.control(ticket_id, owner_id, action)
        return self.store.snapshot(ticket_id, owner_id)

    def claim(self, ticket_id: str, owner_id: str, *, limit: int = 4,
              lease_seconds: int = 90) -> list[dict[str, Any]]:
        ticket = self._ticket(ticket_id, owner_id)
        if ticket["state"] in _TERMINAL_TICKETS:
            return []
        claims = self.store.claim(ticket_id, owner_id, limit=limit, lease_seconds=lease_seconds)
        # A Stop may race with the SQLite claim. Never issue a newly claimed
        # step to an executor after observing the terminal ticket state.
        if self._ticket(ticket_id, owner_id)["state"] in _TERMINAL_TICKETS:
            self.store.control(ticket_id, owner_id, "cancel")
            return []
        return claims

    def heartbeat(self, ticket_id: str, owner_id: str, step_id: str,
                  token: str, *, lease_seconds: int = 90,
                  now: float | None = None) -> bool:
        """Renew an owner-authorized lease without resurrecting stopped work."""
        ticket = self._ticket(ticket_id, owner_id)
        if ticket["state"] in _TERMINAL_TICKETS:
            self.store.control(ticket_id, owner_id, "cancel")
            return False
        renewed = self.store.heartbeat(
            ticket_id, owner_id, step_id, token,
            lease_seconds=lease_seconds, now=now,
        )
        if self._ticket(ticket_id, owner_id)["state"] in _TERMINAL_TICKETS:
            self.store.control(ticket_id, owner_id, "cancel")
            return False
        return renewed

    def complete(self, ticket_id: str, owner_id: str, step_id: str,
                 token: str, *, success: bool, output: str = "") -> bool:
        ticket = self._ticket(ticket_id, owner_id)
        if ticket["state"] in _TERMINAL_TICKETS:
            self.store.control(ticket_id, owner_id, "cancel")
            return False
        return self.store.complete(ticket_id, owner_id, step_id, token,
                                   success=success, output=output)
