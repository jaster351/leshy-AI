"""Регрессия: одновременный первый запрос Web/Telegram получает один scheduler."""
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import leshy.inference_scheduler as module


class SchedulerSingletonRaceTests(unittest.TestCase):
    def test_concurrent_initialization_returns_same_instance(self):
        app = SimpleNamespace()
        start = threading.Barrier(12)
        created = []
        results = []
        failures = []
        original = module.InferenceScheduler

        class SlowScheduler(original):
            def __init__(self):
                time.sleep(0.02)  # Расширяем окно гонки первого создания.
                super().__init__()
                created.append(self)

        def worker():
            try:
                start.wait(timeout=3)
                results.append(module.get_inference_scheduler(app))
            except Exception as exc:
                failures.append(exc)

        with patch.object(module, "InferenceScheduler", SlowScheduler):
            threads = [threading.Thread(target=worker, daemon=True) for _ in range(12)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=4)

        self.assertFalse(failures, failures)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(created), 1)
        self.assertEqual(len(results), 12)
        self.assertTrue(all(result is created[0] for result in results))
        self.assertIs(app.inference_scheduler, created[0])


if __name__ == "__main__":
    unittest.main()
