from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from urllib.parse import parse_qs

from leshy.browser_agent import ApprovalRequired, BrowserAction, BrowserAgent, BrowserRisk
from leshy.playwright_browser_adapter import PlaywrightBrowserAdapter
from leshy.system_browser import find_system_browser


class _ShopState:
    orders: list[dict[str, str]] = []


class _ShopHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _write(self, status: int, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path != "/":
            self._write(404, "not found")
            return
        self._write(
            200,
            """<!doctype html><html><body>
            <h1>Тестовый магазин Лешего</h1>
            <form method="post" action="/complete">
              <label>Имя <input name="name" aria-label="Имя" /></label>
              <label>Товар <input name="item" aria-label="Товар" /></label>
              <button type="submit">Подтвердить тестовый заказ</button>
            </form>
            </body></html>""",
        )

    def do_POST(self):
        if self.path != "/complete":
            self._write(404, "not found")
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
        _ShopState.orders.append(
            {
                "name": (form.get("name") or [""])[0],
                "item": (form.get("item") or [""])[0],
            }
        )
        self._write(200, "<html><body><h1>TEST_ORDER_OK</h1></body></html>")


@unittest.skipUnless(find_system_browser() is not None, "Для Browser E2E нужен системный Chrome/Edge/Chromium")
class BrowserApprovalE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _ShopState.orders = []
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _ShopHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self) -> None:
        _ShopState.orders.clear()

    def test_commit_is_blocked_before_approval_then_real_playwright_submits_once(self) -> None:
        adapter = PlaywrightBrowserAdapter(headless=True, timeout_ms=15_000)
        agent = BrowserAgent([adapter], allowed_hosts=("127.0.0.1",))
        action = BrowserAction(
            name="workflow",
            url=self.base_url + "/",
            risk=BrowserRisk.COMMIT,
            description="Создать безопасный тестовый заказ",
            arguments={
                "allowed_hosts": ("127.0.0.1",),
                "steps": [
                    {"kind": "fill", "label": "Имя", "value": "Михаил"},
                    {"kind": "fill", "label": "Товар", "value": "E2E Browser Agent"},
                    {"kind": "click", "role": "button", "name": "Подтвердить тестовый заказ"},
                    {"kind": "wait_for", "text": "TEST_ORDER_OK"},
                ],
            },
        )

        with self.assertRaises(ApprovalRequired):
            agent.execute(action)
        self.assertEqual(_ShopState.orders, [])

        result = agent.execute(action, approved=True)
        self.assertEqual(result.status, "ok", result.message)
        self.assertIn("TEST_ORDER_OK", result.data.get("visible_text", ""))
        self.assertEqual(
            _ShopState.orders,
            [{"name": "Михаил", "item": "E2E Browser Agent"}],
        )

    def test_foreign_domain_is_blocked_before_browser_navigation(self) -> None:
        adapter = PlaywrightBrowserAdapter(headless=True, timeout_ms=5_000)
        agent = BrowserAgent([adapter], allowed_hosts=("127.0.0.1",))
        result = agent.execute(
            BrowserAction(
                name="read",
                url="https://example.com/",
                risk=BrowserRisk.READ,
                arguments={"allowed_hosts": ("127.0.0.1",)},
            )
        )
        self.assertEqual(result.status, "blocked")
        self.assertEqual(_ShopState.orders, [])


if __name__ == "__main__":
    unittest.main()
