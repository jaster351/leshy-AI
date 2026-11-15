from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from leshy.research_tuning import ResearchTuningStore, manual_research_score


class ResearchTuningTests(unittest.TestCase):
    def test_auto_default_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ResearchTuningStore(root)
            self.assertEqual(store.load(), {"mode": "auto", "level": "5.00"})
            self.assertEqual(store.save("manual", "1.01")["level"], "1.01")
            self.assertEqual(ResearchTuningStore(root).load(), {
                "mode": "manual", "level": "1.01",
            })
            store.save("auto", "0.00")
            self.assertEqual(ResearchTuningStore(root).load()["mode"], "auto")

    def test_decimal_bounds_and_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ResearchTuningStore(tmp)
            for value in ("-0.01", "10.01", "1.001", "NaN", "Infinity", True):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    store.save("manual", value)
            self.assertEqual(store.save("manual", "0")["level"], "0.00")
            self.assertEqual(store.save("manual", "10")["level"], "10.00")
            self.assertEqual(store.save("manual", "0,01")["level"], "0.01")

    def test_bias_affects_ambiguous_decision(self):
        low = manual_research_score("0.00", True, False, "WEB")
        high = manual_research_score("10.00", True, False, "WEB")
        self.assertLess(low["score"], low["threshold"])
        self.assertGreaterEqual(high["score"], high["threshold"])
        self.assertEqual(high["user_bias"], 3.25)
        self.assertEqual(low["user_bias"], -3.25)

    def test_safe_history_without_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ResearchTuningStore(tmp)
            store.record(
                mode="manual", level="1.01", needed=True,
                reason="manual_score",
                weights={"score": 4.2, "threshold": 1.5,
                         "url": "https://secret.example/", "query": "private prompt"},
            )
            persisted = store.decisions_path.read_text(encoding="utf-8")
            self.assertNotIn("secret", persisted)
            self.assertNotIn("private prompt", persisted)
            item = json.loads(persisted)[0]
            self.assertEqual(item["level"], "1.01")
            self.assertEqual(item["weights"]["score"], 4.2)
            self.assertEqual(store.status(1)["decisions"][0]["reason_code"], "manual_score")


if __name__ == "__main__":
    unittest.main()
