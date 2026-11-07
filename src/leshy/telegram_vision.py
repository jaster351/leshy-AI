"""Telegram photo/document-image -> explicit local vision API.

Never route image captions to text-only LLM. Never log image content,
Telegram download URLs or bot credentials. No external vision service.
"""
from __future__ import annotations

import base64
import json
import os
from urllib import error, parse, request

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024


class VisionUnavailable(RuntimeError):
    pass


class VisionNotConfigured(VisionUnavailable):
    pass


class TelegramImageError(RuntimeError):
    pass


def image_attachment(message: dict) -> dict | None:
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        candidates = [x for x in photos if isinstance(x, dict) and x.get("file_id")]
        return max(candidates, key=lambda p: int(p.get("file_size") or 0)) if candidates else None
    document = message.get("document")
    if isinstance(document, dict) and str(document.get("mime_type") or "").lower() in {
        "image/jpeg", "image/png", "image/webp",
    }:
        return document
    return None


def _kind(image: bytes) -> str:
    if image.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        return "image/webp"
    raise TelegramImageError("Неподдерживаемый формат изображения: требуется JPEG, PNG или WebP")


def _local_endpoint() -> tuple[str, str]:
    url = str(os.getenv("LESHY_VISION_ENDPOINT") or "").strip()
    model = str(os.getenv("LESHY_VISION_MODEL") or "").strip()
    if not url or not model:
        raise VisionNotConfigured(
            "Фотографию получил, но локальная vision-модель не настроена. "
            "Укажите LESHY_VISION_ENDPOINT и LESHY_VISION_MODEL для локального "
            "OpenAI-совместимого сервера с поддержкой изображений."
        )
    parsed = parse.urlsplit(url)
    if parsed.scheme != "http" or (parsed.hostname or "").lower() not in {
        "127.0.0.1", "localhost", "::1",
    } or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise VisionNotConfigured(
            "Локальный vision endpoint должен использовать http://127.0.0.1 "
            "или localhost. Передача фотографий на внешние серверы запрещена."
        )
    path = parsed.path.rstrip("/")
    if path in {"", "/v1"}:
        path = "/v1/chat/completions"
    if path != "/v1/chat/completions":
        raise VisionNotConfigured("Vision endpoint должен вести на /v1/chat/completions")
    host = parsed.netloc
    return f"http://{host}{path}", model


class _RejectRedirects(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise VisionUnavailable("Локальный vision backend перенаправил запрос; редирект запрещён")


class LocalTelegramVision:
    def __init__(self, bridge):
        self.bridge = bridge

    def _download(self, attachment: dict) -> bytes:
        file_id = str(attachment.get("file_id") or "").strip()
        if not file_id:
            raise TelegramImageError("Telegram не передал ID изображения")
        if int(attachment.get("file_size") or 0) > MAX_IMAGE_BYTES:
            raise TelegramImageError("Изображение больше 8 МБ")
        try:
            info = self.bridge._api("getFile", {"file_id": file_id}, timeout=12)
        except Exception:
            raise TelegramImageError("Не удалось запросить изображение в Telegram") from None
        if not isinstance(info, dict) or int(info.get("file_size") or 0) > MAX_IMAGE_BYTES:
            raise TelegramImageError("Telegram вернул неверные параметры файла")
        path = str(info.get("file_path") or "")
        parts = path.split("/")
        if not path or path.startswith("/") or "\\" in path or any(x in {"", ".", ".."} for x in parts) or parse.urlsplit(path).scheme:
            raise TelegramImageError("Telegram вернул некорректный путь")
        token = self.bridge._token()
        if not token:
            raise TelegramImageError("Telegram token не настроен")
        url = f"https://api.telegram.org/file/bot{token}/{parse.quote(path, safe='/')}"
        try:
            with request.urlopen(request.Request(url, method="GET"), timeout=20) as response:
                image = response.read(MAX_IMAGE_BYTES + 1)
        except (error.URLError, OSError, TimeoutError):
            # URL содержит секрет bot token: не писать текст исключения в логи.
            raise TelegramImageError("Не удалось скачать фотографию из Telegram") from None
        if not image or len(image) > MAX_IMAGE_BYTES:
            raise TelegramImageError("Файл Telegram пустой или превышает 8 МБ")
        media_type = _kind(image)
        declared = str(attachment.get("mime_type") or "")
        if declared and declared != media_type:
            raise TelegramImageError("Тип файла не совпадает с форматом изображения")
        return image

    def describe(self, message: dict) -> str:
        endpoint, model = _local_endpoint()
        attachment = image_attachment(message)
        if attachment is None:
            raise TelegramImageError("Изображение отсутствует")
        image = self._download(attachment)
        media_type = _kind(image)
        caption = str(message.get("caption") or "").strip()[:2000]
        question = caption or "Опиши, пожалуйста, что изображено на фотографии."
        payload = {
            "model": model,
            "stream": False,
            "temperature": 0.2,
            "max_tokens": 384,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": "data:" + media_type + ";base64," + base64.b64encode(image).decode("ascii")}},
                ],
            }],
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        opener = request.build_opener(_RejectRedirects())
        try:
            with opener.open(
                request.Request(endpoint, data=data, headers={"Content-Type": "application/json"}, method="POST"),
                timeout=45,
            ) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except (error.URLError, OSError, TimeoutError):
            raise VisionUnavailable("Локальный vision backend недоступен или превысил лимит 45 секунд") from None
        if len(body) > MAX_RESPONSE_BYTES:
            raise VisionUnavailable("Vision backend вернул слишком большой ответ")
        try:
            result = json.loads(body)
            choice = result["choices"][0]
            content = choice["message"]["content"]
            if isinstance(content, list):
                content = " ".join(str(x.get("text") or "") for x in content if isinstance(x, dict))
            content = str(content or "").strip()
        except (ValueError, TypeError, LookupError):
            raise VisionUnavailable("Vision backend вернул неверный формат ответа") from None
        if not content:
            raise VisionUnavailable("Vision backend вернул пустое описание изображения")
        return content[:12000]


__all__ = ["LocalTelegramVision", "image_attachment", "VisionUnavailable", "VisionNotConfigured", "TelegramImageError"]
