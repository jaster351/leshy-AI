"""Bounded, provider-authenticated interactive SSH PTY sessions.

No SSH transport is reachable through the LLM. Every input line must receive
an exact, single-use Web approval. Nothing from terminal output is persisted.
The remote terminal uses the existing Paramiko transport and pinned SSH key;
no Docker, Guacamole, daemon, or target-host agent is installed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import codecs
import hashlib
import hmac
import secrets
import threading
import time
from typing import Any

from .superagent_remote_permissions import RemotePermissionPolicy, VerifiedActor
from .superagent_ssh_approvals import (
    _file, consume_ssh_approval, prepare_ssh_command,
)
from .superagent_ssh_terminal import (
    _registered_pin, check_terminal_permission, verified_connection,
)

_MAX_OUTPUT = 32 * 1024
_MAX_INPUT = 2048
_SESSION_TTL = 600.0


def _line(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Введите одну SSH-команду")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("В интерактивный терминал принимается одна строка без управляющих символов")
    if len(value.encode("utf-8")) > _MAX_INPUT:
        raise ValueError("SSH-команда слишком длинная")
    return value


def _signature(sid: str, line: str) -> str:
    return "ssh-pty-v1:" + sid + ":" + hashlib.sha256(
        _line(line).encode("utf-8")
    ).hexdigest()


@dataclass
class _PTY:
    owner: str
    fingerprint: str
    node: str
    credential_file: str
    host_pin: str
    context: Any
    channel: Any
    expires: float
    decoder: Any = field(default_factory=lambda: codecs.getincrementaldecoder("utf-8")("replace"))


class InteractiveSSHBroker:
    """In-process PTY broker; no implicit replay, reconnection, or saved output."""

    def __init__(self, *, max_sessions: int = 3, ttl_seconds: float = _SESSION_TTL,
                 clock=time.monotonic):
        if not 1 <= max_sessions <= 8 or not 30 <= ttl_seconds <= 1800:
            raise ValueError("Недопустимые ограничения SSH-сеансов")
        self._lock = threading.RLock()
        self._sessions: dict[str, _PTY] = {}
        self._max = max_sessions
        self._ttl = ttl_seconds
        self._clock = clock
        self._stopped = threading.Event()
        threading.Thread(target=self._janitor, name="leshy-ssh-pty-janitor",
                         daemon=True).start()

    def _janitor(self) -> None:
        while not self._stopped.wait(15.0):
            with self._lock:
                self._prune()

    @staticmethod
    def _disconnect(session: _PTY) -> None:
        try:
            session.channel.close()
        except Exception:
            pass
        try:
            session.context.__exit__(None, None, None)
        except Exception:
            pass

    def _prune(self) -> None:
        now = self._clock()
        for sid, session in list(self._sessions.items()):
            if session.expires <= now or bool(getattr(session.channel, "closed", False)):
                self._sessions.pop(sid, None)
                self._disconnect(session)

    def shutdown(self) -> None:
        self._stopped.set()
        with self._lock:
            for session in self._sessions.values():
                self._disconnect(session)
            self._sessions.clear()

    def _get(self, policy: RemotePermissionPolicy, actor: VerifiedActor,
             session_id: str, *, touch: bool = True) -> _PTY:
        policy._check_actor(actor)
        self._prune()
        if not isinstance(session_id, str):
            raise PermissionError("SSH-сеанс недоступен")
        session = self._sessions.get(session_id)
        if (session is None
                or not hmac.compare_digest(session.owner, actor.owner_id)
                or not hmac.compare_digest(
                    session.fingerprint, actor.session_fingerprint
                )):
            raise PermissionError("SSH-сеанс недоступен")
        # Recheck grant and SSH host-key registration for EVERY read/write.
        policy.require(actor, session.node, "execute",
                       observed_fingerprint=session.host_pin)
        if touch:
            session.expires = self._clock() + self._ttl
        return session

    def open(self, policy: RemotePermissionPolicy, actor: VerifiedActor,
             node_id: str, credential_file: str) -> dict[str, str]:
        node = check_terminal_permission(policy, actor, node_id)
        filename = _file(credential_file)
        pin = _registered_pin(policy, actor, node, "execute")
        # Network handshakes may take seconds. Never hold the broker lock
        # while connecting: established PTYs must remain readable/closable.
        with self._lock:
            self._prune()
            if self._stopped.is_set():
                raise RuntimeError("SSH-брокер остановлен")
            if len(self._sessions) >= self._max:
                raise PermissionError("Достигнут предел активных SSH-сеансов")

        context = verified_connection(node, filename, pin, policy, actor,
                                      "execute", 8.0)
        channel = None
        entered = False
        registered = False
        sid = ""
        try:
            transport, observed = context.__enter__()
            entered = True
            channel = transport.open_session(timeout=8.0)
            channel.settimeout(8.0)
            channel.get_pty(term="xterm", width=100, height=30)
            channel.invoke_shell()
            policy.require(actor, node, "execute",
                           observed_fingerprint=observed)
            sid = secrets.token_urlsafe(24)
            with self._lock:
                # Another concurrent opener or shutdown can win the race
                # while our SSH handshake is in progress.
                self._prune()
                if self._stopped.is_set():
                    raise RuntimeError("SSH-брокер остановлен")
                if len(self._sessions) >= self._max:
                    raise PermissionError("Достигнут предел активных SSH-сеансов")
                # Authorization may also have been revoked during connect.
                policy.require(actor, node, "execute",
                               observed_fingerprint=observed)
                self._sessions[sid] = _PTY(
                    actor.owner_id, actor.session_fingerprint, node, filename,
                    observed, context, channel, self._clock() + self._ttl,
                )
                registered = True
                try:
                    # Do not expose the session to a concurrent reader before
                    # the mandatory audit record was successfully persisted.
                    policy.storage.audit("superagent.ssh.pty.open", {
                        "node_id": node, "interactive": True,
                    })
                except BaseException:
                    self._sessions.pop(sid, None)
                    registered = False
                    raise
            return {"session_id": sid, "node_id": node,
                    "expires_seconds": int(self._ttl)}
        except BaseException:
            if not registered:
                try:
                    if channel is not None:
                        channel.close()
                finally:
                    # __enter__ may fail before yielding; in that case the
                    # generator context manager already performed cleanup.
                    if entered:
                        context.__exit__(None, None, None)
            raise

    def read(self, policy: RemotePermissionPolicy, actor: VerifiedActor,
             session_id: str) -> dict[str, Any]:
        with self._lock:
            # Automatic UI polling must not extend the session lifetime.
            session = self._get(policy, actor, session_id, touch=False)
            chunks = bytearray()
            while len(chunks) < _MAX_OUTPUT and session.channel.recv_ready():
                part = session.channel.recv(min(4096, _MAX_OUTPUT - len(chunks)))
                if not part:
                    break
                chunks.extend(part)
            truncated = bool(session.channel.recv_ready())
            return {
                "text": session.decoder.decode(bytes(chunks), final=False),
                "more_output": truncated,
                "session_open": not bool(getattr(session.channel, "closed", False)),
            }

    def prepare(self, policy: RemotePermissionPolicy, actor: VerifiedActor,
                session_id: str, line: str) -> dict[str, Any]:
        with self._lock:
            session = self._get(policy, actor, session_id)
            return prepare_ssh_command(
                policy, actor, session.node, session.credential_file,
                _signature(session_id, line), approval_kind="pty-input",
            )

    def write(self, policy: RemotePermissionPolicy, actor: VerifiedActor,
              session_id: str, line: str, approval_token: str) -> None:
        # Session ID is part of the approved digest; receipts cannot be moved
        # between independent interactive shells on the same registered host.
        command = _line(line)
        with self._lock:
            session = self._get(policy, actor, session_id)
            consume_ssh_approval(
                policy, actor, approval_token, session.node,
                session.credential_file, _signature(session_id, command),
                approval_kind="pty-input",
            )
            session = self._get(policy, actor, session_id)
            # No automatic replay on send failure: the remote command may
            # already have been processed in full or in part.
            session.channel.sendall(command.encode("utf-8") + b"\n")
            policy.storage.audit("superagent.ssh.pty.input", {
                "node_id": session.node,
                "command_sha256": hashlib.sha256(
                    command.encode("utf-8")
                ).hexdigest(),
            })

    def close(self, policy: RemotePermissionPolicy, actor: VerifiedActor,
              session_id: str) -> None:
        # An enrolled owner may close even after their per-node grant expires.
        policy._check_actor(actor)
        with self._lock:
            session = self._sessions.get(session_id) if isinstance(session_id, str) else None
            if (session is None or session.owner != actor.owner_id
                    or not hmac.compare_digest(
                        session.fingerprint, actor.session_fingerprint
                    )):
                raise PermissionError("SSH-сеанс недоступен")
            self._sessions.pop(session_id, None)
            self._disconnect(session)
            policy.storage.audit("superagent.ssh.pty.close", {
                "node_id": session.node,
            })


__all__ = ["InteractiveSSHBroker"]
