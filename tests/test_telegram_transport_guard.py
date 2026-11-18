"""Регрессии защищённого Telegram HTTP-транспорта (без реальной сети)."""
from urllib import error, request
from unittest.mock import patch

import pytest

from leshy.telegram_proxy import TelegramProxyTransport


def test_system_proxy_path_uses_redirect_safe_opener(tmp_path):
    transport = TelegramProxyTransport(tmp_path)
    req = request.Request("https://api.telegram.org/botSECRET/getMe")
    with patch.object(transport, "_read", return_value={"enabled": False}), \
         patch("leshy.telegram_proxy.request.build_opener") as opener:
        opener.return_value.open.return_value = object()
        transport.open(req, timeout=2)
    assert any(type(handler).__name__ == "_NoRedirects"
               for handler in opener.call_args.args)


@pytest.mark.parametrize("url", [
    "https://example.com/botSECRET/getMe",
    "http://api.telegram.org/botSECRET/getMe",
    "https://api.telegram.org:443/botSECRET/getMe",
    "https://user:pass@api.telegram.org/botSECRET/getMe",
    "https://api.telegram.org/botSECRET/getMe#fragment",
])
def test_rejects_untrusted_url_before_reading_secrets(tmp_path, url):
    transport = TelegramProxyTransport(tmp_path)
    with patch.object(transport, "_read") as read:
        with pytest.raises(ValueError):
            transport.open(request.Request(url), timeout=1)
        read.assert_not_called()


@pytest.mark.parametrize("status", [403, 429, 503])
def test_http_error_preserves_metadata_without_exposing_token(tmp_path, status):
    from io import BytesIO
    transport = TelegramProxyTransport(tmp_path)
    secret_url = "https://api.telegram.org/botSECRET/getMe"
    original = error.HTTPError(
        secret_url, status, "failed", {"Retry-After": "7"}, BytesIO(b"failure")
    )
    with patch.object(transport, "_read", return_value={"enabled": False}), \
         patch("leshy.telegram_proxy.request.build_opener") as opener:
        opener.return_value.open.side_effect = original
        with pytest.raises(error.HTTPError) as caught:
            transport.open(request.Request(secret_url), timeout=2)
    result = caught.value
    assert result.code == status
    assert result.headers["Retry-After"] == "7"
    assert result.read() == b"failure"
    assert "SECRET" not in result.geturl()
    assert "SECRET" not in str(result)


def test_redirect_rejected_without_leaking_token(tmp_path):
    from leshy.telegram_proxy import _NoRedirects

    transport = TelegramProxyTransport(tmp_path)
    url = "https://api.telegram.org/botSECRET/getMe"
    with patch.object(transport, "_read", return_value={"enabled": False}), \
         patch("leshy.telegram_proxy.request.build_opener") as opener:
        opener.return_value.open.side_effect = lambda *_args, **_kwargs: (
            _NoRedirects().redirect_request(
                request.Request(url), None, 302, "redirect",
                {"Location": "https://evil.example/botSECRET"}, "https://evil.example/botSECRET"
            )
        )
        with pytest.raises(ConnectionError) as caught:
            transport.open(request.Request(url), timeout=2)
    assert "SECRET" not in str(caught.value)
    assert "evil.example" not in str(caught.value)


def test_tls_verification_failure_does_not_expose_token(tmp_path):
    import ssl

    transport = TelegramProxyTransport(tmp_path)
    url = "https://api.telegram.org/botSECRET/getMe"
    tls_error = ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED")
    with patch.object(transport, "_read", return_value={"enabled": False}), \
         patch("leshy.telegram_proxy.request.build_opener") as opener:
        opener.return_value.open.side_effect = error.URLError(tls_error)
        with pytest.raises(ConnectionError) as caught:
            transport.open(request.Request(url), timeout=2)
    assert "CERTIFICATE_VERIFY_FAILED" in str(caught.value)
    assert "SECRET" not in str(caught.value)
