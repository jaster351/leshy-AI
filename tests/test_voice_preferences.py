import unittest

from leshy.voice_preferences import DEFAULT_VOICE_GENDER, VoicePreference, normalize_voice_gender


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


if __name__ == "__main__":
    unittest.main()
