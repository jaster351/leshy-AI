"""Regression: ordinary chat messages must reach the LLM, not canned routes."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from leshy.config import Settings
from leshy.runtime import Runtime
from leshy.realtime_chat import RealtimeChatManager
from leshy.owner_knowledge_capture import is_owner_document_status_request
from leshy.dialogue_rules import is_rule_inspection_request
from leshy.github_facts import is_leshy_repository_audit_request


class _RecordingModel:
    model = "test-recording-model"

    def __init__(self):
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        return "Ответ сформирован нейросетью по исходному вопросу."

    def stream_chat(self, messages, cancel_event=None):
        self.calls.append(messages)
        yield "Ответ сформирован нейросетью по исходному вопросу."


class ModelFirstRoutingTests(unittest.TestCase):
    def settings(self, root):
        brain = root / "LeshyBrain"
        return Settings(
            install_root=root,
            brain_dir=brain,
            data_dir=brain / "data",
            database_path=brain / "data" / "leshy.sqlite3",
            native_models_dir=brain / "models",
            workspace_root=brain / "workspace",
            llm_backend="echo",
            embeddings_enabled=False,
        )

    def test_long_assignment_does_not_trigger_stored_documents(self):
        long_task = (
            "Леший, это самостоятельное задание по развитию Knowledge.\n"
            "Проверь обучение, сохранение инструкций, правила общения и GitHub.\n"
            "Инструкцию Cloud4box мы уже обсуждали, но это не основная тема.\n"
            + "Не подменяй задачу проверкой одного документа. " * 12
        )
        self.assertFalse(is_owner_document_status_request(long_task))
        self.assertFalse(is_rule_inspection_request(long_task))
        self.assertFalse(is_leshy_repository_audit_request(long_task))

    def test_short_explicit_status_still_works_as_tool(self):
        self.assertTrue(is_owner_document_status_request(
            "Проверь, сохранились ли постоянные правила Cloud4box в Knowledge?"
        ))
        self.assertFalse(is_owner_document_status_request(
            "Проверь Knowledge, инструкцию и систему самообучения."
        ))

    def test_chat_uses_model_for_cloud4box_status(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Runtime(self.settings(Path(folder)))
            model = _RecordingModel()
            app.llm = model
            reply = app.chat(
                "Проверь, сохранилась ли инструкция Cloud4box в Knowledge?",
                owner_id="web:test-model-first", session_id="owner-model",
            )
            self.assertEqual(reply["answer"], "Ответ сформирован нейросетью по исходному вопросу.")
            self.assertEqual(len(model.calls), 1)
            system = "\n".join(x.content for x in model.calls[0] if x.role == "system")
            self.assertIn("owner_document_read", system)

    def test_stream_uses_model_for_cloud4box_status(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Runtime(self.settings(Path(folder)))
            model = _RecordingModel()
            app.llm = model
            stream = list(RealtimeChatManager(app).stream(
                "Проверь, сохранилась ли инструкция Cloud4box в Knowledge?",
                owner_id="web:test-model-first", session_id="owner-stream",
            ))
            completed = [x.data for x in stream if x.type == "done"]
            self.assertTrue(completed)
            self.assertEqual(completed[-1].get("answer"), "Ответ сформирован нейросетью по исходному вопросу.")
            self.assertTrue(model.calls)
            system = "\n".join(x.content for x in model.calls[0] if x.role == "system")
            self.assertIn("owner_document_read", system)


if __name__ == "__main__":
    unittest.main()
