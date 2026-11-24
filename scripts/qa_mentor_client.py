"""CLI-клиент для реального диалога QA/наставника с Лешим.

Транспорт только к /v1/qa-mentor/chat. При недоступности Лешего завершает
работу с ошибкой; не создаёт подставные ответы.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib import error, request
from urllib.parse import urlsplit


def main() -> int:
    parser = argparse.ArgumentParser(description="Реальный QA-диалог с Лешим")
    parser.add_argument("--role", choices=["mentor", "qa"], required=True)
    parser.add_argument("--message", default="", help="Текст сообщения (или stdin)")
    parser.add_argument("--session-id", default="")
    args = parser.parse_args()

    address = os.getenv("LESHY_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("LESHY_API_TOKEN", "").strip()
    if not address or not token:
        print("NOT_RUN: требуются LESHY_BASE_URL и LESHY_API_TOKEN", file=sys.stderr)
        return 2
    parts = urlsplit(address)
    if (parts.scheme not in {"http", "https"} or not parts.netloc
            or parts.username or parts.password or parts.path not in {"", "/"}
            or parts.query or parts.fragment):
        print("NOT_RUN: неверный адрес LESHY_BASE_URL", file=sys.stderr)
        return 2
    if parts.scheme == "http" and parts.hostname not in {"127.0.0.1", "::1", "localhost"}:
        print("NOT_RUN: внешний сервер требует HTTPS", file=sys.stderr)
        return 2

    message = args.message or sys.stdin.read(1700)
    message = message.strip()
    if not 1 <= len(message) <= 1600:
        print("NOT_RUN: сообщение должно содержать 1–1600 символов", file=sys.stderr)
        return 2

    body = json.dumps({
        "role": args.role, "message": message, "session_id": args.session_id,
    }, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        address + "/v1/qa-mentor/chat",
        data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
        },
    )
    class RejectRedirect(request.HTTPRedirectHandler):
        # Never forward the machine bearer credential to another origin.
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise error.HTTPError(req.full_url, code, "Redirect denied", headers, fp)

    opener = request.build_opener(RejectRedirect)
    try:
        with opener.open(req, timeout=120) as response:
            payload = json.load(response)
    except error.HTTPError as exc:
        print("FAIL: API вернул HTTP " + str(exc.code), file=sys.stderr)
        return 1
    except (error.URLError, TimeoutError, OSError, ValueError) as exc:
        print("NOT_RUN: реального ответа API нет (" + type(exc).__name__ + ")", file=sys.stderr)
        return 1

    if payload.get("source") != "live_api" or not payload.get("record_id"):
        print("FAIL: сервер не подтвердил запись реального запроса", file=sys.stderr)
        return 1
    print(json.dumps({
        "status": "answered", "role": args.role,
        "answer": payload.get("answer") or "",
        "record_id": payload["record_id"],
        "session_id": payload.get("session_id") or "",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
