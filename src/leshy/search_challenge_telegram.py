from __future__ import annotations

"""Send a screenshot of the active challenge to the configured Telegram owner.

Bot API media upload uses the SAME proxy/TLS transport as all other messages.
There are no public screenshot URLs, no bot-token logging and no re-upload of
the user's personal browser profile or session cookies.
"""

import base64
import json
import secrets
from urllib import request


def send_challenge_photo(telegram, chat_id: str, screenshot_data_url: str,
                         engine: str, url: str = "") -> dict:
    """Upload PNG via Telegram multipart/form-data and optionally add URL button."""
    prefix = "data:image/png;base64,"
    if not isinstance(screenshot_data_url, str) or not screenshot_data_url.startswith(prefix):
        raise ValueError("Снимок CAPTCHA пока недоступен")
    encoded = screenshot_data_url[len(prefix):]
    # Telegram limits photos; cap payload before decode to avoid unbounded memory.
    if not encoded or len(encoded) > 12_000_000:
        raise ValueError("Недопустимый размер снимка CAPTCHA")
    try:
        image = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error):
        raise ValueError("Повреждённый снимок CAPTCHA") from None
    if not image.startswith(b"\x89PNG\r\n\x1a\n") or len(image) > 9_000_000:
        raise ValueError("Снимок CAPTCHA не является допустимым PNG")

    token = telegram._token()
    if not token:
        raise RuntimeError("Telegram token не настроен")

    caption = (
        "Леший: поисковик " + str(engine)[:50] + " запросил подтверждение.\n"
        "На фото — текущая CAPTCHA. Откройте проверку и выполните действия. "
        "После подтверждения поиск продолжится автоматически. "
        "Ссылка действительна 10 минут."
    )
    payload = {"chat_id": str(chat_id), "caption": caption}
    # Telegram inline buttons require an externally reachable HTTPS URL.
    if str(url).startswith("https://"):
        payload["reply_markup"] = json.dumps({
            "inline_keyboard": [[{"text": "🔐 Решить CAPTCHA", "url": url}]]
        }, ensure_ascii=False)

    boundary = "leshy_" + secrets.token_hex(16)
    result: list[bytes] = []
    for key, value in payload.items():
        result.append((
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8"))
    result.append((
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="photo"; filename="search-verification.png"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode("ascii"))
    result.append(image)
    result.append(f"\r\n--{boundary}--\r\n".encode("ascii"))
    req = request.Request(
        "https://api.telegram.org/bot" + token + "/sendPhoto",
        data=b"".join(result),
        method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
    )
    # Proxy config, CA validation and authentication are identical to text sends.
    with telegram.proxy.open(req, timeout=15) as response:
        answer = json.loads(response.read().decode("utf-8"))
    if not answer.get("ok"):
        raise RuntimeError("Telegram не принял изображение проверки")
    return answer.get("result") or {}


__all__ = ["send_challenge_photo"]
