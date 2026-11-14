"""Server-side authorization boundary shared by future SSH/RDP executors.

This is deliberately NOT an SSH/RDP transport or a public endpoint.
Call it only after a transport has obtained and verified the *observed*
remote host key. Never pass an owner_id, an address-only identity, or an
unverified claimed fingerprint from a chat prompt.

The Web session and node permissions are rechecked immediately before the
action. Mutating actions require a separate server-side approval verifier,
which is fail-closed until an approval workflow is connected.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

from .superagent_remote_permissions import RemotePermissionPolicy, VerifiedActor

_Result = TypeVar("_Result")
_SENSITIVE = frozenset({"execute", "update", "reboot", "rdp"})
_CAPABILITIES = frozenset({"inspect"}) | _SENSITIVE


@dataclass(frozen=True, slots=True)
class AuthorizedRemoteAction:
    """Audit-safe evidence of one authorization decision, never a reusable token."""

    owner_id: str
    node_id: str
    capability: str
    action_digest: str = ""


class RemoteActionBoundary(Generic[_Result]):
    """Synchronous in-process gate for trusted, server-owned transport code.

    * `web_cookie` comes only from the verified HTTP cookie header.
    * `observed_fingerprint` comes from the authenticated SSH host-key
      handshake or equivalent trusted transport identity, not the user.
    * `operation` is a function selected by server code, never an LLM-supplied
      command or arbitrary callback accepted from a remote HTTP client.
    * `approval_verifier` is a trusted server-side receipt validator that
      checks a one-time approval scoped to actor, node, capability and action.
      A boolean or user-provided approval text must never count as approval.

    No network or filesystem access is performed here.
    """

    def __init__(
        self,
        policy: RemotePermissionPolicy,
        *,
        approval_verifier: Callable[[VerifiedActor, str, str, str, str], bool] | None = None,
    ) -> None:
        self._policy = policy
        self._approval_verifier = approval_verifier

    def perform(
        self,
        *,
        web_cookie: str,
        node_id: str,
        capability: str,
        observed_fingerprint: str,
        operation: Callable[[AuthorizedRemoteAction], _Result],
        approval_receipt: str = "",
        action_digest: str = "",
    ) -> _Result:
        """Perform one action only after independently validating all scopes.

        Recheck permissions *after* approval validation to close the common
        revoke/expiry race while the owner is confirming a dangerous action.
        The SSH/RDP transport must additionally recheck before each subsequent
        remote command, not retain a successful gate decision as a session grant.
        """
        cap = str(capability or "").strip().lower()
        if cap not in _CAPABILITIES:
            raise PermissionError("Удалённое действие не поддерживается")
        if not callable(operation):
            raise TypeError("Требуется серверный обработчик действия")
        actor = self._policy.actor_from_web_cookie(web_cookie)
        # Verify live session and enrolled, attested node *before* examining
        # a potentially sensitive approval receipt or invoking transport code.
        self._policy.require(
            actor, node_id, cap, observed_fingerprint=observed_fingerprint
        )
        if cap in _SENSITIVE:
            receipt = str(approval_receipt or "").strip()
            digest = str(action_digest or "").strip().lower()
            # SHA-256 of a canonical server-built action specification. A
            # confirmation for one command must never authorize another.
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise PermissionError("Не указан проверяемый отпечаток удалённого действия")
            if not receipt or not callable(self._approval_verifier):
                raise PermissionError("Для удалённого действия требуется подтверждение")
            if not self._approval_verifier(actor, node_id, cap, digest, receipt):
                raise PermissionError("Подтверждение не найдено, истекло или отозвано")
            # Approval lookup can take time; node access might have been
            # revoked or the Web session terminated meanwhile.
            self._policy.require(
                actor, node_id, cap, observed_fingerprint=observed_fingerprint
            )
        context = AuthorizedRemoteAction(
            owner_id=actor.owner_id,
            node_id=self._policy._node(node_id),
            capability=cap,
            action_digest=str(action_digest or "").strip().lower() if cap in _SENSITIVE else "",
        )
        return operation(context)


__all__ = ["AuthorizedRemoteAction", "RemoteActionBoundary"]
