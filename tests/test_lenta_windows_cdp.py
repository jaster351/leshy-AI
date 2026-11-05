from __future__ import annotations

import asyncio
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from leshy.browser_agent import BrowserAction, BrowserRisk
from leshy.browser_profile_cdp import profile_cdp_url
from leshy.browser_use_adapter import BrowserUseAdapter
from leshy.lenta_browser_session import LentaBrowserSession
from leshy.lenta_runtime import _basket_task, _parse_explicit_intent


class ProfileCDPTests(unittest.TestCase):
    def test_valid_local_debugger_is_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "DevToolsActivePort").write_text(
                "49321\n/devtools/browser/exact-uuid\n", encoding="utf-8"
            )
            response = {
                "webSocketDebuggerUrl":
                    "ws://127.0.0.1:49321/devtools/browser/exact-uuid",
            }
            client = types.SimpleNamespace(
                open=lambda url, timeout: BytesIO(json.dumps(response).encode("utf-8"))
            )
            with patch("leshy.browser_profile_cdp.request.build_opener", return_value=client):
                self.assertEqual(profile_cdp_url(Path(tmp)), "http://127.0.0.1:49321")

    def test_stale_debugger_is_not_used_for_another_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "DevToolsActivePort").write_text(
                "49321\n/devtools/browser/original\n", encoding="utf-8"
            )
            client = types.SimpleNamespace(
                open=lambda url, timeout: BytesIO(json.dumps({
                    "webSocketDebuggerUrl": "ws://127.0.0.1:49321/devtools/browser/other"
                }).encode("utf-8"))
            )
            with patch("leshy.browser_profile_cdp.request.build_opener", return_value=client):
                self.assertIsNone(profile_cdp_url(tmp))

    def test_absent_or_invalid_port_is_not_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(profile_cdp_url(tmp))
            (Path(tmp) / "DevToolsActivePort").write_text(
                "0\n/devtools/browser/uuid\n", encoding="utf-8"
            )
            self.assertIsNone(profile_cdp_url(tmp))


class LentaLoginCDPTests(unittest.TestCase):
    def test_login_launches_debuggable_custom_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = LentaBrowserSession(tmp, "telegram:123")
            system = types.SimpleNamespace(name="Microsoft Edge", executable=r"C:\Edge\msedge.exe")
            with patch("leshy.lenta_browser_session.ensure_system_browser", return_value=system), \
                 patch("leshy.lenta_browser_session.profile_cdp_url", side_effect=[
                     None, "http://127.0.0.1:49321"
                 ]), \
                 patch("leshy.lenta_browser_session.profile_may_be_in_use", return_value=False), \
                 patch("leshy.lenta_browser_session.subprocess.Popen") as spawn:
                spawn.return_value.poll.return_value = None
                result = session.open_login_window()
                argv = spawn.call_args.args[0]
            self.assertIn("--remote-debugging-port=0", argv)
            self.assertIn("--remote-debugging-address=127.0.0.1", argv)
            self.assertIn(f"--user-data-dir={session.profile_dir}", argv)
            self.assertFalse(result["reused"])

    def test_open_auth_window_is_reused_without_relaunch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = LentaBrowserSession(tmp, "telegram:123")
            system = types.SimpleNamespace(name="Microsoft Edge", executable="msedge.exe")
            with patch("leshy.lenta_browser_session.ensure_system_browser", return_value=system), \
                 patch("leshy.lenta_browser_session.profile_cdp_url", return_value="http://127.0.0.1:49321"), \
                 patch("leshy.lenta_browser_session.subprocess.Popen") as spawn:
                result = session.open_login_window()
            spawn.assert_not_called()
            self.assertTrue(result["reused"])

    def test_locked_legacy_window_requires_safe_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = LentaBrowserSession(tmp, "telegram:123")
            system = types.SimpleNamespace(name="Microsoft Edge", executable="msedge.exe")
            with patch("leshy.lenta_browser_session.ensure_system_browser", return_value=system), \
                 patch("leshy.lenta_browser_session.profile_cdp_url", return_value=None), \
                 patch("leshy.lenta_browser_session.profile_may_be_in_use", return_value=True), \
                 patch("leshy.lenta_browser_session.subprocess.Popen") as spawn:
                with self.assertRaisesRegex(RuntimeError, "профил"):
                    session.open_login_window()
            spawn.assert_not_called()


class BrowserUseCDPTests(unittest.TestCase):
    def test_agent_attaches_to_the_same_lenta_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            observed: list[dict] = []
            class FakeBrowser:
                def __init__(self, **kwargs):
                    observed.append(kwargs)
                def stop(self):
                    return None
            class FakeAgent:
                def __init__(self, **kwargs):
                    self.kwargs = kwargs
                async def run(self, **kwargs):
                    return types.SimpleNamespace(final_result=lambda: "Товар добавлен в корзину")
            stub = types.ModuleType("browser_use")
            stub.Browser = FakeBrowser
            stub.Agent = FakeAgent
            action = BrowserAction(
                "agent_mutate", "https://lenta.com/", BrowserRisk.MUTATE,
                {
                    "task": "Добавь самое дешёвое мороженое в корзину",
                    "allowed_hosts": ("lenta.com",),
                    "profile_namespace": "lenta",
                    "profile_key": "user-profile",
                },
            )
            system = types.SimpleNamespace(name="Microsoft Edge", executable="msedge.exe")
            with patch.dict(os.environ, {"LESHY_BRAIN_DIR": tmp}), \
                 patch.dict(sys.modules, {"browser_use": stub}), \
                 patch("leshy.browser_use_adapter.ensure_system_browser", return_value=system), \
                 patch("leshy.browser_use_adapter.profile_cdp_url", return_value="http://127.0.0.1:49321"), \
                 patch.object(BrowserUseAdapter, "_tools_for_action", return_value=object()):
                adapter = BrowserUseAdapter(llm=object(), headless=False)
                result = asyncio.run(adapter._run(action))
            self.assertEqual(result.status, "ok")
            self.assertEqual(observed[0]["cdp_url"], "http://127.0.0.1:49321")
            self.assertTrue(observed[0]["keep_alive"])
            self.assertNotIn("user_data_dir", observed[0])
            self.assertNotIn("executable_path", observed[0])

    def test_cart_instruction_requires_one_cheapest_product_and_no_checkout(self) -> None:
        instruction = _basket_task("Добавь самое дешёвое мороженое")
        self.assertIn("РОВНО ОДНУ", instruction)
        self.assertIn("сравни цены", instruction)
        self.assertIn("НЕ нажимай финальную", instruction)

    def test_telegram_icecream_command_is_basket_not_login(self) -> None:
        intent = _parse_explicit_intent(
            "https://lenta.com/ Зайди на сайт и добавь мне в корзину "
            "мой профиль самое дешёвое мороженое"
        )
        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "basket")

    def test_personal_cabinet_words_do_not_override_cart_operation(self) -> None:
        intent = _parse_explicit_intent(
            "Лента, добавь мороженое в корзину в моём личном кабинете"
        )
        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "basket")

    def test_explicit_login_still_works(self) -> None:
        intent = _parse_explicit_intent("Лента, войти в личный кабинет")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "login")


if __name__ == "__main__":
    unittest.main()
