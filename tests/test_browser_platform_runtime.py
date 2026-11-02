from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from leshy.action_approvals import ApprovalBroker
from leshy.browser_agent import ApprovalRequired, BrowserAction, BrowserActionResult, BrowserAgent, BrowserRisk
from leshy.browser_platform_runtime import BrowserPlatform, parse_browser_conversation_intent
from leshy.browser_use_adapter import BrowserUseAdapter
from leshy.document_intelligence import DocumentIntelligence
from leshy.extension_marketplace import ExtensionApprovalRequired, ExtensionMarketplace, MarketplaceEntry
from leshy.playwright_browser_adapter import PlaywrightBrowserAdapter
from leshy.storage import Storage
from leshy.tools_gateway import McpToolsGateway, ToolApprovalRequired, ToolManifest, ToolPermission, ToolRegistry


class _CaptureAdapter:
    adapter_id = "capture"

    def __init__(self):
        self.last = None

    def available(self) -> bool:
        return True

    def execute(self, action: BrowserAction) -> BrowserActionResult:
        self.last = action
        return BrowserActionResult("ok", "done", {"approved": action.arguments.get("_approval_verified")})


class _FakeApp:
    def __init__(self, root: Path):
        self.storage = Storage(root / "leshy.db")
        self.llm = object()


class BrowserConversationIntentTests(unittest.TestCase):
    def test_product_search_is_research_first(self) -> None:
        intent = parse_browser_conversation_intent("Найди мне хороший ноутбук в магазине и сравни цены")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "research")

    def test_initial_buy_request_is_research_first_even_without_generic_noun(self) -> None:
        intent = parse_browser_conversation_intent("Купи мне iPhone 18 Pro")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "research")

    def test_initial_booking_request_is_research_first(self) -> None:
        intent = parse_browser_conversation_intent("Забронируй мне отель в Казани на выходные")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "research")

    def test_followup_selects_previous_variant(self) -> None:
        intent = parse_browser_conversation_intent("Забронируй второй вариант")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "commit")
        self.assertEqual(intent.selected_index, 1)

    def test_financial_intent_extracts_amount(self) -> None:
        intent = parse_browser_conversation_intent("Оплати первый вариант 12 500 ₽")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "financial")
        self.assertEqual(intent.selected_index, 0)
        self.assertIn("12 500", intent.amount_summary)

    def test_financial_intent_without_amount_is_still_financial(self) -> None:
        intent = parse_browser_conversation_intent("Оплати первый вариант")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "financial")
        self.assertEqual(intent.amount_summary, "")


class ApprovalBrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.broker = ApprovalBroker(ttl_seconds=60)
        self.action = BrowserAction(
            "agent_commit",
            "https://shop.example/checkout",
            BrowserRisk.COMMIT,
            {"task": "оформить заказ"},
            "Оформить заказ",
        )

    def test_read_action_never_gets_approval_token(self) -> None:
        with self.assertRaises(ValueError):
            self.broker.request("owner", BrowserAction("research", "https://example.com", BrowserRisk.READ))

    def test_approval_is_owner_bound_and_one_time(self) -> None:
        pending = self.broker.request("owner-a", self.action)
        with self.assertRaises(KeyError):
            self.broker.consume("owner-b", pending.approval_id)
        action = self.broker.consume("owner-a", pending.approval_id)
        self.assertEqual(action.url, self.action.url)
        with self.assertRaises(KeyError):
            self.broker.consume("owner-a", pending.approval_id)

    def test_public_payload_does_not_expose_arguments(self) -> None:
        secret_action = BrowserAction(
            "agent_financial",
            "https://pay.example",
            BrowserRisk.FINANCIAL,
            {"task": "pay", "secret": "do-not-leak"},
            "Оплата",
        )
        public = self.broker.request("owner", secret_action, summary="Оплата 100 ₽").public()
        self.assertNotIn("arguments", public)
        self.assertNotIn("do-not-leak", str(public))


class BrowserAgentApprovalTests(unittest.TestCase):
    def test_commit_requires_gate_and_adapter_receives_verified_marker(self) -> None:
        adapter = _CaptureAdapter()
        agent = BrowserAgent([adapter])
        action = BrowserAction("agent_commit", "https://example.com", BrowserRisk.COMMIT, {"task": "send"})
        with self.assertRaises(ApprovalRequired):
            agent.execute(action)
        result = agent.execute(action, approved=True)
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.data["approved"])
        self.assertTrue(adapter.last.arguments["_approval_verified"])
        self.assertNotIn("_approval_verified", action.arguments)

    def test_browser_use_rejects_unverified_nonread_without_starting_browser(self) -> None:
        adapter = BrowserUseAdapter(llm=object())
        action = BrowserAction("agent_commit", "https://example.com", BrowserRisk.COMMIT, {"task": "send"})
        result = adapter.execute(action)
        self.assertEqual(result.status, "blocked")

    def test_financial_requires_exact_approval_summary(self) -> None:
        adapter = BrowserUseAdapter(llm=object())
        action = BrowserAction(
            "agent_financial",
            "https://example.com",
            BrowserRisk.FINANCIAL,
            {"task": "pay", "_approval_verified": True},
        )
        result = adapter.execute(action)
        self.assertEqual(result.status, "blocked")

    def test_explicit_allowlist_cannot_be_widened_by_start_url(self) -> None:
        action = BrowserAction(
            "research",
            "https://evil.example/path",
            BrowserRisk.READ,
            {"allowed_hosts": ("trusted.example",)},
        )
        self.assertEqual(BrowserUseAdapter._hosts(action), ("trusted.example",))
        self.assertEqual(PlaywrightBrowserAdapter._allowed_hosts(action), ("trusted.example",))
        self.assertFalse(BrowserUseAdapter._host_allowed(action.url, BrowserUseAdapter._hosts(action)))


class BrowserResearchRecoveryTests(unittest.TestCase):
    def test_selected_results_survive_platform_recreation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"LESHY_BRAIN_DIR": str(root / "brain")}, clear=False):
                app = _FakeApp(root)
                first = BrowserPlatform(app)
                first._remember_results(
                    "session-1",
                    [
                        {"title": "Первый", "url": "https://one.example/item", "snippet": "one"},
                        {"title": "Второй", "url": "https://two.example/item", "snippet": "two"},
                    ],
                )
                second = BrowserPlatform(app)
                restored = second._last_results("session-1")
                self.assertEqual([item["title"] for item in restored], ["Первый", "Второй"])
                intent = parse_browser_conversation_intent("Купи второй вариант")
                self.assertEqual(second._selected_url(intent, "session-1"), "https://two.example/item")


class DocumentIntelligenceTests(unittest.TestCase):
    def test_local_text_document_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.txt"
            path.write_text("Леший умеет читать документы.", encoding="utf-8")
            result = DocumentIntelligence().convert(path)
            self.assertIn("Леший", result.markdown)
            self.assertIn(result.engine, {"markitdown", "text"})

    def test_remote_url_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            DocumentIntelligence().convert("https://example.com/file.pdf")


class ToolsRegistryTests(unittest.TestCase):
    def test_registry_roundtrip_and_disable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(Path(tmp) / "registry.json")
            manifest = ToolManifest(
                tool_id="demo",
                title="Demo",
                transport="stdio",
                command="python",
                args=("-m", "demo"),
                permissions=(ToolPermission.READ,),
            )
            registry.register(manifest)
            self.assertEqual(registry.get("demo").args, ("-m", "demo"))
            registry.set_enabled("demo", False)
            self.assertFalse(registry.get("demo").enabled)

    def test_insecure_remote_http_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(Path(tmp) / "registry.json")
            with self.assertRaises(ValueError):
                registry.register(ToolManifest(tool_id="remote", title="Remote", transport="http", url="http://example.com/mcp"))

    def test_dangerous_mcp_tool_requires_approval_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(Path(tmp) / "registry.json")
            registry.register(
                ToolManifest(
                    tool_id="danger",
                    title="Danger",
                    transport="stdio",
                    command="never-run",
                    permissions=(ToolPermission.MUTATE,),
                )
            )
            gateway = McpToolsGateway(registry)
            with self.assertRaises(ToolApprovalRequired):
                gateway.call("danger", "do_it", {})


class ExtensionMarketplaceTests(unittest.TestCase):
    def test_inspect_requires_pinned_version_and_license(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marketplace = ExtensionMarketplace(ToolRegistry(Path(tmp) / "tools.json"), root=Path(tmp) / "extensions")
            with self.assertRaises(ValueError):
                marketplace.inspect(
                    MarketplaceEntry(
                        extension_id="demo",
                        title="Demo",
                        package_spec="demo>=1",
                        module="demo.server",
                        repository="https://github.com/example/demo",
                        license="MIT",
                    )
                )

    def test_install_requires_approval_before_creating_venv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marketplace = ExtensionMarketplace(ToolRegistry(Path(tmp) / "tools.json"), root=Path(tmp) / "extensions")
            entry = MarketplaceEntry(
                extension_id="demo",
                title="Demo",
                package_spec="demo==1.2.3",
                module="demo.server",
                repository="https://github.com/example/demo",
                license="MIT",
            )
            info = marketplace.inspect(entry)
            self.assertTrue(info["requires_approval"])
            with self.assertRaises(ExtensionApprovalRequired):
                marketplace.install(entry, approved=False)
            self.assertFalse((Path(tmp) / "extensions" / "demo").exists())


if __name__ == "__main__":
    unittest.main()
