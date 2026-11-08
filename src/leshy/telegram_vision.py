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
        if not candidates:
            return None
        # Telegram даёт несколько версий фото: выбираем наибольшую
        # разрешённую, чтобы слишком большой оригинал не ломал обработку.
        def file_size(item):
            try:
                return max(0, int(item.get("file_size") or 0))
            except (ValueError, TypeError, OverflowError):
                return MAX_IMAGE_BYTES + 1
        allowed = [item for item in candidates if file_size(item) <= MAX_IMAGE_BYTES]
        return max(allowed or candidates, key=file_size)
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


class _RejectRedirects(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise VisionUnavailable("Локальный vision backend перенаправил запрос; редирект запрещён")


def _loopback_origin(url: str) -> str:
    parsed = parse.urlsplit(str(url or "").strip())
    if (
        parsed.scheme != "http"
        or (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise VisionNotConfigured("Ollama vision доступен только на локальном адресе http://127.0.0.1")
    try:
        port = parsed.port
    except ValueError as exc:
        raise VisionNotConfigured("Некорректный локальный порт vision") from exc
    if port is not None and not 1 <= port <= 65535:
        raise VisionNotConfigured("Некорректный локальный порт vision")
    return parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _local_endpoint() -> tuple[str, str]:
    """Explicit OpenAI-compatible loopback backend, never an external API."""
    url = str(os.getenv("LESHY_VISION_ENDPOINT") or "").strip()
    model = str(os.getenv("LESHY_VISION_MODEL") or "").strip()
    if not url or not model:
        raise VisionNotConfigured("LESHY_VISION_ENDPOINT и LESHY_VISION_MODEL должны быть заданы вместе")
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
    return f"http://{parsed.netloc}{path}", model


def _ollama_vision_backend(*, preferred: str = "") -> tuple[str, str] | None:
    """Discover already installed vision-capable local models using /api/show."""
    base = _loopback_origin(os.getenv("LESHY_VISION_OLLAMA_URL", "http://127.0.0.1:11434"))
    opener = request.build_opener(request.ProxyHandler({}), _RejectRedirects())
    try:
        with opener.open(request.Request(base + "/api/tags", method="GET"), timeout=2.0) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            return None
        tags = json.loads(raw)
        available = tags.get("models", [])
        if not isinstance(available, list):
            return None
    except (error.URLError, OSError, TimeoutError, ValueError, TypeError, VisionUnavailable):
        return None

    names = []
    for row in available[:100]:
        name = str(row.get("name") or row.get("model") or "").strip() if isinstance(row, dict) else ""
        if 1 <= len(name) <= 120 and name not in names:
            names.append(name)
    if preferred:
        names = [name for name in names if name == preferred or name.split(":")[0] == preferred]

    def rank(name: str) -> tuple[int, str]:
        hints = ("qwen2.5vl:3b", "qwen3-vl", "qwen2.5vl", "qwen3.5",
                 "gemma3", "llava", "minicpm-v", "internvl", "moondream")
        return next((i for i, hint in enumerate(hints) if hint in name.lower()), len(hints)), name.lower()

    for name in sorted(names, key=rank)[:16]:
        req = request.Request(
            base + "/api/show", data=json.dumps({"model": name}).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"},
        )
        try:
            with opener.open(req, timeout=2.5) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                continue
            metadata = json.loads(raw)
            capabilities = metadata.get("capabilities", [])
            if isinstance(capabilities, list) and "vision" in capabilities:
                return base + "/api/chat", name
        except (error.URLError, OSError, TimeoutError, ValueError, TypeError, VisionUnavailable):
            continue
    return None


def _vision_backend() -> tuple[str, str, str]:
    explicit_endpoint = str(os.getenv("LESHY_VISION_ENDPOINT") or "").strip()
    preferred = str(os.getenv("LESHY_VISION_MODEL") or "").strip()
    if explicit_endpoint:
        endpoint, model = _local_endpoint()
        return "openai", endpoint, model
    found = _ollama_vision_backend(preferred=preferred)
    if found is not None:
        return "ollama", *found
    if preferred:
        raise VisionNotConfigured(
            f"Локальная vision-модель {preferred!r} не найдена в Ollama или не поддерживает изображения. "
            "Проверьте, что Ollama запущена и модель установлена."
        )
    raise VisionNotConfigured(
        "Фотографию получил, но подходящая локальная vision-модель не найдена. "
        "Установите Ollama и выполните: ollama pull qwen2.5vl:3b. "
        "После установки Леший обнаружит модель автоматически, без LESHY_VISION_ENDPOINT. "
        "Существующий llama.cpp GGUF может требовать отдельный мультимодальный mmproj."
    )


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
            with self.bridge.proxy.open(request.Request(url, method="GET"), timeout=20) as response:
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
        backend, endpoint, model = _vision_backend()
        attachment = image_attachment(message)
        if attachment is None:
            raise TelegramImageError("Изображение отсутствует")
        image = self._download(attachment)
        media_type = _kind(image)
        caption = str(message.get("caption") or "").strip()[:2000]
        question = caption or "Опиши, пожалуйста, что изображено на фотографии."
        encoded = base64.b64encode(image).decode("ascii")
        if backend == "ollama":
            payload = {
                "model": model,
                "stream": False,
                "messages": [{"role": "user", "content": question, "images": [encoded]}],
                "options": {"temperature": 0.2, "num_predict": 384},
            }
        else:
            payload = {
                "model": model,
                "stream": False,
                "temperature": 0.2,
                "max_tokens": 384,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {"type": "image_url", "image_url": {"url": "data:" + media_type + ";base64," + encoded}},
                    ],
                }],
            }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        # Запрещаем HTTP_PROXY/HTTPS_PROXY для чувствительных image bytes.
        # Endpoint разрешён только на loopback, редиректы также запрещены.
        opener = request.build_opener(request.ProxyHandler({}), _RejectRedirects())
        try:
            with opener.open(
                request.Request(endpoint, data=data, headers={"Content-Type": "application/json"}, method="POST"),
                timeout=90,
            ) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except error.HTTPError as exc:
            if exc.code in {400, 422}:
                raise VisionUnavailable(
                    "Vision backend отклонил мультимодальный запрос (HTTP 400/422). "
                    "Проверьте, что модель поддерживает изображения и формат image_url."
                ) from None
            if exc.code == 404:
                raise VisionUnavailable(
                    "Vision endpoint или указанная модель не найдены (HTTP 404)."
                ) from None
            raise VisionUnavailable(
                f"Локальный vision backend вернул HTTP {exc.code}."
            ) from None
        except (error.URLError, OSError, TimeoutError):
            raise VisionUnavailable(
                "Локальный vision backend недоступен или превысил лимит 90 секунд"
            ) from None
        if len(body) > MAX_RESPONSE_BYTES:
            raise VisionUnavailable("Vision backend вернул слишком большой ответ")
        try:
            result = json.loads(body)
            if backend == "ollama":
                content = result["message"]["content"]
            else:
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
