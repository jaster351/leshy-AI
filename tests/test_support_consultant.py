from pathlib import Path
import tempfile
import unittest

from leshy.llm import ChatMessage
from leshy.modules import ModuleRequest
from leshy.support_consultant import (
    ClientTicketContext,
    DiagnosticResult,
    SupportConsultantModule,
    TicketMessage,
)


WORK_METADATA = {
    "event": "support_ticket_notice",
    "event_time": "2026-11-02T09:00:00+00:00",  # Понедельник, 12:00 МСК.
    "manual_work_mode": True,
}


class FakeSource:
    def __init__(self, context: ClientTicketContext):
        self.context = context
        self.calls = 0

    def load_context(self, notice):
        self.calls += 1
        return self.context


class FakeDiagnostics:
    def __init__(self):
        self.calls = 0

    def run(self, context):
        self.calls += 1
        return [DiagnosticResult("TCP/22", "тайм-аут", "Соединение не установлено за 5 с")]


class FakeLLM:
    def __init__(self):
        self.calls: list[list[ChatMessage]] = []

    def chat(self, messages):
        self.calls.append(messages)
        return "1. Основной ответ\n2. Короткий ответ\n3. Осторожный ответ"


class SupportConsultantTests(unittest.TestCase):
    def _context(self, *, client_id="42", email="client@example.test"):
        return ClientTicketContext(
            ticket_id="1001",
            client_id=client_id,
            email=email,
            subject="Не подключается сервер",
            service_name="Linux VPS",
            server_ip="203.0.113.10",
            operating_system="Ubuntu",
            messages=(
                TicketMessage("Клиент", "Не могу подключиться по SSH", "10:00"),
                TicketMessage("Поддержка", "Уточните IP-адрес", "10:05"),
                TicketMessage("Клиент", "203.0.113.10", "10:06"),
            ),
        )

    def _metadata(self, **extra):
        value = dict(WORK_METADATA)
        value.update(
            {
                "ticket_id": "1001",
                "client_id": "42",
                "email": "client@example.test",
                "question": "SSH не работает",
            }
        )
        value.update(extra)
        return value

    def test_prepares_answer_only_after_diagnostics(self):
        source = FakeSource(self._context())
        diagnostics = FakeDiagnostics()
        llm = FakeLLM()
        module = SupportConsultantModule(source, diagnostics, llm)

        response = module.handle(
            ModuleRequest(
                text="Новая заявка",
                channel="telegram",
                metadata=self._metadata(),
            )
        )

        self.assertTrue(response.handled)
        self.assertEqual(response.metadata["status"], "prepared")
        self.assertTrue(response.metadata["read_only"])
        self.assertTrue(response.metadata["notify"])
        self.assertEqual(source.calls, 1)
        self.assertEqual(diagnostics.calls, 1)
        self.assertEqual(len(llm.calls), 1)
        prompt = llm.calls[0][-1].content
        self.assertIn("TCP/22: тайм-аут", prompt)
        self.assertIn("Не могу подключиться по SSH", prompt)
        self.assertIn("РОВНО ОДИН готовый ответ", prompt)
        self.assertEqual(llm.calls[0][1].role, "system")
        self.assertIn("поставщика услуг", llm.calls[0][1].content)
        self.assertIn("не добавляй подпись", llm.calls[0][1].content)

    def test_identity_conflict_stops_before_diagnostics_and_llm(self):
        source = FakeSource(self._context(client_id="99"))
        diagnostics = FakeDiagnostics()
        llm = FakeLLM()
        module = SupportConsultantModule(source, diagnostics, llm)

        response = module.handle(
            ModuleRequest(text="Новая заявка", metadata=self._metadata())
        )

        self.assertEqual(response.metadata["status"], "identity_conflict")
        self.assertEqual(diagnostics.calls, 0)
        self.assertEqual(llm.calls, [])

    def test_outside_shift_suppresses_everything(self):
        source = FakeSource(self._context())
        diagnostics = FakeDiagnostics()
        llm = FakeLLM()
        module = SupportConsultantModule(source, diagnostics, llm)

        response = module.handle(
            ModuleRequest(
                text="Новая заявка",
                channel="telegram",
                metadata=self._metadata(event_time="2026-11-02T19:00:00+00:00"),  # 22:00 МСК.
            )
        )

        self.assertEqual(response.text, "")
        self.assertEqual(response.metadata["status"], "work_assistance_suppressed")
        self.assertFalse(response.metadata["notify"])
        self.assertEqual(source.calls, 0)
        self.assertEqual(diagnostics.calls, 0)
        self.assertEqual(llm.calls, [])

    def test_without_work_session_suppresses_notifications(self):
        source = FakeSource(self._context())
        diagnostics = FakeDiagnostics()
        llm = FakeLLM()
        module = SupportConsultantModule(source, diagnostics, llm)

        response = module.handle(
            ModuleRequest(
                text="Новая заявка",
                channel="telegram",
                metadata=self._metadata(manual_work_mode=False, workstation_active=False),
            )
        )

        self.assertEqual(response.metadata["status"], "work_assistance_suppressed")
        self.assertFalse(response.metadata["notify"])
        self.assertEqual(source.calls, 0)

    def test_audit_uses_only_central_log_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "LeshyBrain"
            source = FakeSource(self._context())
            diagnostics = FakeDiagnostics()
            llm = FakeLLM()
            module = SupportConsultantModule(source, diagnostics, llm, brain_dir=brain)

            module.handle(
                ModuleRequest(
                    text="Новая заявка",
                    metadata=self._metadata(),
                )
            )

            log_path = brain / "logs" / "support-consultant.jsonl"
            self.assertTrue(log_path.is_file())
            content = log_path.read_text(encoding="utf-8")
            self.assertNotIn("client@example.test", content)
            self.assertNotIn("203.0.113.10", content)
            self.assertNotIn("Не могу подключиться", content)


if __name__ == "__main__":
    unittest.main()
