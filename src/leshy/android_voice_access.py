"""Dedicated, least-privilege text conversation for the Android voice client.

This endpoint NEVER accepts an owner_id, an administrative API token, or
requests to run tools. Audio remains on the Android device.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
from datetime import datetime, timezone

from .llm import ChatMessage
from .storage import utcnow


_CHAT_LOCK = threading.RLock()
_FILE = "android_voice_access.json"


def _file(brain_dir: Path) -> Path:
    return Path(brain_dir) / "secrets" / _FILE


def issue_token(brain_dir: Path, *, rotate: bool = False) -> str:
    """Provision from the owner's local terminal only, never over HTTP."""
    dest = _file(brain_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not rotate:
        raise FileExistsError("Android voice token exists. Use --rotate to revoke it first.")
    token = secrets.token_urlsafe(32)
    payload = {
        "schema": 1,
        "sha256": sha256(token.encode("ascii")).hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    temp = dest.with_name(dest.name + "." + secrets.token_hex(6) + ".tmp")
    try:
        fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(payload, out, ensure_ascii=True)
        os.replace(temp, dest)
    finally:
        temp.unlink(missing_ok=True)
    return token


def authorized_owner(brain_dir: Path, bearer: str) -> str | None:
    prefix = "Bearer "
    if not bearer.startswith(prefix):
        return None
    token = bearer[len(prefix):]
    if not 32 <= len(token) <= 128 or not token.isascii() or any(c.isspace() for c in token):
        return None
    try:
        payload = json.loads(_file(brain_dir).read_text(encoding="utf-8"))
        if payload.get("schema") != 1:
            return None
        fingerprint = str(payload.get("sha256", ""))
        provided = sha256(token.encode("ascii")).hexdigest()
        if len(fingerprint) != 64 or not hmac.compare_digest(fingerprint, provided):
            return None
        return "android-voice:" + fingerprint[:24]
    except (OSError, ValueError, TypeError, KeyError):
        return None


def conversational_answer(app, owner_id: str, body: dict) -> dict:
    """No OS actions, browser actions, SuperAgent commands, or owner escalation."""
    if set(body) - {"message", "session_id"}:
        raise ValueError("Unsupported Android chat fields")
    message = str(body.get("message") or "").strip()
    if not 1 <= len(message) <= 2500:
        raise ValueError("Message must contain 1 to 2500 characters")
    if message.startswith("/"):
        raise ValueError("Commands are unavailable through the Android voice endpoint")
    session = str(body.get("session_id") or "").strip()
    if len(session) > 120 or any(c not in "0123456789abcdef-" for c in session.lower()):
        raise ValueError("Invalid Android session_id")
    tracker = getattr(app, "conversation_activity", None)
    if tracker is not None:
        tracker.begin()
    try:
        with _CHAT_LOCK:
            sid = app._ensure_session(owner_id, session or None)
            with app.storage.connection() as conn:
                conn.execute(
                    "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                    (sid, "user", message, utcnow()),
                )
                conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (utcnow(), sid))
            history = app.history(owner_id, sid, limit=14)
            trusted = app.knowledge.search(message, trusted_only=True, limit=5)
            facts = "\n".join(
                "- " + str(item.get("claim", ""))[:500] + " [" +
                str(item.get("source_url", ""))[:200] + "]"
                for item in trusted
            )
            messages = [
                ChatMessage(
                    "system",
                    "Ты Леший, работающая нейросеть проекта владельца. "
                    "Это обычный голосовой диалог Android-клиента. Отвечай на языке "
                    "пользователя, естественно, кратко, без выдуманных фактов. "
                    "Не выполняй команды, не вызывай инструменты и не заявляй об "
                    "исполнении действий. При нехватке актуальных данных сообщи "
                    "об ограничении. Этот канал предоставляет только диалог."
                ),
            ]
            if facts:
                messages.append(ChatMessage("system", "Проверенные Knowledge-факты:\n" + facts))
            messages += [
                ChatMessage(str(row["role"]), str(row["content"]))
                for row in history if row["role"] in ("user", "assistant")
            ]
            answer = str(app.llm.chat(messages) or "").strip()
            if not answer:
                raise RuntimeError("Model returned an empty answer")
            app._save_assistant(sid, answer)
            return {"session_id": sid, "answer": answer}
    finally:
        if tracker is not None:
            tracker.end()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or rotate Android voice-only access")
    parser.add_argument("--brain-dir", type=Path, required=True)
    parser.add_argument("--rotate", action="store_true")
    args = parser.parse_args()
    token = issue_token(args.brain_dir, rotate=args.rotate)
    print("ANDROID_VOICE_TOKEN=" + token)
    print("Store on your phone; this one-time token is NOT your Leshy admin API token.")
    print("Rotating the token immediately revokes the previous one.")


if __name__ == "__main__":
    main()
