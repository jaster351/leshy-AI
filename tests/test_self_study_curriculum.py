from __future__ import annotations

import unittest

from leshy.scientific_learning import (
    CURRICULUM_CAPACITY,
    SELF_STUDY_TASK_TARGET,
    scientific_query,
)


class SelfStudyCurriculumTests(unittest.TestCase):
    def test_capacity_covers_million_unique_tasks_with_large_reserve(self):
        self.assertEqual(SELF_STUDY_TASK_TARGET, 1_000_000)
        self.assertGreater(CURRICULUM_CAPACITY, SELF_STUDY_TASK_TARGET * 30)

    def test_old_480_task_cycle_no_longer_repeats(self):
        first = scientific_query(0)
        formerly_repeated = scientific_query(480)
        self.assertNotEqual(first.query, formerly_repeated.query)
        self.assertNotEqual(first.task_id, formerly_repeated.task_id)

    def test_large_sample_has_no_duplicates(self):
        # Проверяем достаточно большой непрерывный диапазон и границы нескольких
        # mixed-radix осей без дорогого создания миллионов строк в CI.
        queries = {scientific_query(i).query for i in range(25_000)}
        self.assertEqual(len(queries), 25_000)

    def test_learning_does_not_stop_at_million_target(self):
        before = scientific_query(SELF_STUDY_TASK_TARGET - 1)
        after = scientific_query(SELF_STUDY_TASK_TARGET)
        self.assertEqual(before.task_id, SELF_STUDY_TASK_TARGET - 1)
        self.assertEqual(after.task_id, SELF_STUDY_TASK_TARGET)
        self.assertNotEqual(before.query, after.query)

    def test_capacity_boundary_fails_closed_instead_of_wrapping(self):
        last = scientific_query(CURRICULUM_CAPACITY - 1)
        self.assertEqual(last.task_id, CURRICULUM_CAPACITY - 1)
        with self.assertRaises(IndexError):
            scientific_query(CURRICULUM_CAPACITY)

    def test_task_contains_meaningful_dimensions(self):
        task = scientific_query(123_456)
        self.assertIn(task.topic, task.query)
        self.assertIn(task.lens, task.query)
        self.assertIn(task.evidence_mode, task.query)
        self.assertIn(task.cross_focus, task.query)
        self.assertIn(task.comparison_topic, task.query)
        self.assertNotEqual(task.topic, task.comparison_topic)
        self.assertIn(f"site:{task.domain}", task.query)


if __name__ == "__main__":
    unittest.main()
