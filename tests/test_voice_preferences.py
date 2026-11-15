from pathlib import Path
import tempfile
import unittest

from leshy.voice_preferences import (
    DEFAULT_VOICE_GENDER,
    VoicePreference,
    VoicePreferenceStore,
    normalize_voice_gender,
)


class VoicePreferenceTests(unittest.TestCase):
    def test_default_is_male(self):
        self.assertEqual(DEFAULT_VOICE_GENDER, "male")
        self.assertEqual(VoicePreference().effective_gender, "male")

    def test_empty_or_invalid_value_falls_back_to_male(self):
        for value in (None, "", "unknown", "robot"):
            with self.subTest(value=value):
                self.assertEqual(normalize_voice_gender(value), "male")

    def test_explicit_female_choice_is_preserved(self):
        self.assertEqual(normalize_voice_gender("female"), "female")
        self.assertEqual(VoicePreference("female").effective_gender, "female")

    def test_saved_choice_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "LeshyBrain"
            first_process = VoicePreferenceStore(brain)
            self.assertEqual(first_process.effective_gender(), "male")
            first_process.save("female")

            second_process = VoicePreferenceStore(brain)
            self.assertEqual(second_process.effective_gender(), "female")

    def test_user_choice_can_be_changed_back_to_male(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "LeshyBrain"
            store = VoicePreferenceStore(brain)
            store.save("female")
            store.save("male")
            self.assertEqual(VoicePreferenceStore(brain).effective_gender(), "male")


if __name__ == "__main__":
    unittest.main()
