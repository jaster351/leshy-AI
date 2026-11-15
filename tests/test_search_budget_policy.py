"""Search budget must treat explicit web requests differently from casual prompts."""
import unittest

from leshy.interactive_search_budget import (
    INTERACTIVE_SEARCH_BUDGET_SECONDS,
    _search_budget_for_reason,
)


class SearchBudgetTests(unittest.TestCase):
    def test_explicit_search_receives_longer_budget(self):
        self.assertGreater(
            _search_budget_for_reason("явный запрос интернет-поиска"),
            INTERACTIVE_SEARCH_BUDGET_SECONDS,
        )
        self.assertEqual(_search_budget_for_reason("прямой запрос пользователя"), 18.0)

    def test_casual_and_background_search_remains_fast(self):
        self.assertEqual(
            _search_budget_for_reason("срок актуальности проверенных знаний истёк"),
            12.0,
        )
        self.assertEqual(_search_budget_for_reason(""), 12.0)


if __name__ == "__main__":
    unittest.main()
