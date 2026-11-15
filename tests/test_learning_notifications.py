from pathlib import Path
import tempfile
import unittest

from leshy.learning_notifications import (
    LearningEvent,
    LearningNotificationCenter,
    LearningNotificationModule,
)
from leshy.modules import ModuleManager, ModuleRequest


class FakeSink:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, text: str) -> None:
        self.messages.append(text)


class LearningNotificationTests(unittest.TestCase):
    def test_all_learning_logs_live_under_central_log_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "LeshyBrain"
            center = LearningNotificationCenter(brain)
            center.record(LearningEvent("ТСПУ", "Проверены независимые источники"))

            self.assertEqual(center.events_path.parent, brain / "logs")
            self.assertEqual(center.digest_path.parent, brain / "logs")
            self.assertTrue(center.events_path.is_file())
            self.assertTrue(center.digest_path.is_file())

    def test_important_event_is_sent_immediately_and_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            sink = FakeSink()
            center = LearningNotificationCenter(Path(tmp) / "LeshyBrain", sink)
            center.record(
                LearningEvent(
                    topic="ТСПУ",
                    summary="Найдена новая подтверждённая закономерность",
                    importance="important",
                    sources_checked=4,
                    accepted_facts=2,
                )
            )

            self.assertEqual(len(sink.messages), 1)
            self.assertIn("ТСПУ", sink.messages[0])
            self.assertIn("источников проверено: 4", sink.messages[0])
            self.assertTrue(center.events_path.read_text(encoding="utf-8").strip())
            self.assertFalse(center.digest_path.exists())

    def test_normal_events_are_grouped_into_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            sink = FakeSink()
            center = LearningNotificationCenter(Path(tmp) / "LeshyBrain", sink)
            center.record(LearningEvent("Сети", "Изучена диагностика маршрутов"))
            center.record(LearningEvent("Linux", "Уточнено поведение systemd"))

            self.assertEqual(sink.messages, [])
            digest = center.flush_digest()

            self.assertIn("Сводка обучения Лешего", digest)
            self.assertIn("Сети", digest)
            self.assertIn("Linux", digest)
            self.assertEqual(sink.messages, [digest])
            self.assertEqual(center.digest_path.read_text(encoding="utf-8"), "")

    def test_detail_event_is_logged_without_notification_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            sink = FakeSink()
            center = LearningNotificationCenter(Path(tmp) / "LeshyBrain", sink)
            center.record(LearningEvent("HTTP", "Проверен один технический нюанс", importance="detail"))

            self.assertEqual(sink.messages, [])
            self.assertTrue(center.events_path.is_file())
            self.assertFalse(center.digest_path.exists())

    def test_module_answers_learning_progress_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            center = LearningNotificationCenter(Path(tmp) / "LeshyBrain")
            center.record(LearningEvent("SSH", "Уточнена проверка доступности порта 22", importance="detail"))
            manager = ModuleManager()
            manager.register(LearningNotificationModule(center))

            response = manager.dispatch(ModuleRequest("Лёша, чему ты научился сегодня?"))

            self.assertTrue(response.handled)
            self.assertIn("SSH", response.text)
            self.assertIn("порта 22", response.text)


if __name__ == "__main__":
    unittest.main()
