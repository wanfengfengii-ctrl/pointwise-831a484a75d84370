"""Unit tests and an independent naive randomized oracle."""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from temporal_quantile import INT32_MAX, INT32_MIN, Index


class NaiveOracle:
    """Deliberately simple reference implementation used only for testing."""

    def __init__(self, days: int) -> None:
        self.days = days
        self.observations = {}

    def put(self, observation_id, start, end, value):
        self.observations[observation_id] = (start, end, value)

    def delete(self, observation_id):
        self.observations.pop(observation_id, None)

    def values_at(self, day):
        values = []
        for start, end, value in self.observations.values():
            if start <= day < end:
                values.append(value)
        return values

    def median(self, day):
        values = sorted(self.values_at(day))
        if not values:
            return None
        return values[(len(values) - 1) // 2]


class TemporalQuantileTest(unittest.TestCase):
    def test_required_correction_example(self):
        index = Index(3)
        index.put("a", 0, 3, 10)
        index.put("b", 1, 2, 2)
        self.assertEqual(index.median(0), 10)
        self.assertEqual(index.median(1), 2)
        self.assertEqual(index.median(2), 10)

        index.put("b", 2, 3, 20)
        self.assertEqual(index.median(0), 10)
        self.assertEqual(index.median(1), 10)
        self.assertEqual(index.median(2), 10)

    def test_empty_day_and_boundary_half_open_intervals(self):
        index = Index(4)
        index.put("x", 0, 2, 5)
        index.put("y", 2, 4, 7)
        self.assertEqual(index.median(0), 5)
        self.assertEqual(index.median(1), 5)
        self.assertEqual(index.median(2), 7)
        self.assertEqual(index.median(3), 7)

    def test_duplicates_are_counted_independently(self):
        index = Index(2)
        for observation_id, value in [
            ("a", 3),
            ("b", 3),
            ("c", 100),
            ("d", -5),
        ]:
            index.put(observation_id, 0, 2, value)

        # Sorted values are [-5, 3, 3, 100]; lower median is index 1.
        self.assertEqual(index.median(0), 3)
        self.assertEqual(index.median(1), 3)

    def test_large_signed_32_bit_values_and_even_odd_medians(self):
        index = Index(1)
        values = [INT32_MAX, 0, INT32_MIN, -1, 7, 7]
        for position, value in enumerate(values):
            index.put(str(position), 0, 1, value)
        sorted_values = sorted(values)
        self.assertEqual(index.median(0), sorted_values[(len(values) - 1) // 2])

        index.put("extra", 0, 1, -2)
        sorted_values.append(-2)
        sorted_values.sort()
        self.assertEqual(index.median(0), sorted_values[(len(sorted_values) - 1) // 2])

    def test_replace_and_delete(self):
        index = Index(5)
        index.put("a", 0, 5, 1)
        index.put("b", 0, 5, 2)
        index.put("c", 0, 5, 3)
        self.assertEqual(index.median(4), 2)

        index.put("b", 0, 1, 200)
        self.assertEqual(index.median(0), 3)
        self.assertEqual(index.median(1), 1)
        self.assertEqual(index.median(4), 1)

        index.delete("a")
        self.assertEqual(index.median(0), 3)
        self.assertEqual(index.median(1), 3)
        self.assertEqual(index.median(4), 3)

    def test_missing_delete_is_noop_and_returns_none(self):
        index = Index(2)
        self.assertIsNone(index.delete("missing"))
        index.put("a", 0, 1, 9)
        self.assertIsNone(index.delete("missing"))
        self.assertEqual(index.median(0), 9)

    def test_freed_storage_does_not_grow_with_replacements(self):
        index = Index(16)
        index.put("hot", 0, 1, 0)
        for value in range(2000):
            index.put("hot", 0, 1, value)

        active_segments = sum(
            1 for node in range(len(index.seg_left))
            if node not in index.seg_free
            and (
                index.seg_bag[node] != -1
                or index.seg_left[node] != -1
                or index.seg_right[node] != -1
            )
        )
        active_treaps = sum(
            1 for node in range(len(index.treap_key))
            if node not in index.treap_free
        )
        self.assertLessEqual(active_segments, 8)
        self.assertEqual(active_treaps, 1)
        # Allocated slots themselves must remain bounded after churn.
        self.assertEqual(len(index.treap_key) - len(index.treap_free), 1)
        self.assertEqual(len(index.seg_left) - len(index.seg_free), active_segments)
        self.assertEqual(index.median(0), 1999)

        index.delete("hot")
        self.assertIsNone(index.median(0))
        self.assertEqual(index._root, -1)

    def test_invalid_arguments_leave_state_unchanged(self):
        index = Index(3)
        index.put("a", 0, 3, 10)
        invalid_cases = [
            (lambda: Index(0),),
            (lambda: Index(32769),),
            (lambda: Index(True),),
            (lambda: index.put("", 0, 1, 1),),
            (lambda: index.put(1, 0, 1, 1),),
            (lambda: index.put("x", True, 1, 1),),
            (lambda: index.put("x", -1, 1, 1),),
            (lambda: index.put("x", 3, 4, 1),),
            (lambda: index.put("x", 0, 0, 1),),
            (lambda: index.put("x", 2, 1, 1),),
            (lambda: index.put("x", 0, 4, 1),),
            (lambda: index.put("x", 0, 1, True),),
            (lambda: index.put("x", 0, 1, INT32_MIN - 1),),
            (lambda: index.put("x", 0, 1, INT32_MAX + 1),),
            (lambda: index.delete(""),),
            (lambda: index.delete(123),),
            (lambda: index.median(-1),),
            (lambda: index.median(3),),
            (lambda: index.median(False),),
        ]
        for (operation,) in invalid_cases:
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError):
                    operation()

        self.assertEqual(index._observations, {"a": (0, 3, 10)})
        for day in range(3):
            self.assertEqual(index.median(day), 10)

    def test_randomized_against_naive_oracle(self):
        for seed in range(30):
            rng = random.Random(1000 + seed)
            days = rng.randint(1, 40)
            index = Index(days)
            oracle = NaiveOracle(days)
            ids = [f"id-{i}" for i in range(rng.randint(1, 18))]

            for _ in range(400):
                operation = rng.randrange(5)
                observation_id = rng.choice(ids)

                if operation <= 2:
                    start = rng.randrange(days)
                    end = rng.randrange(start + 1, days + 1)
                    value = rng.randint(INT32_MIN, INT32_MAX)
                    index.put(observation_id, start, end, value)
                    oracle.put(observation_id, start, end, value)
                elif operation == 3:
                    index.delete(observation_id)
                    oracle.delete(observation_id)
                else:
                    day = rng.randrange(days)
                    self.assertEqual(index.median(day), oracle.median(day))

            for day in range(days):
                self.assertEqual(index.median(day), oracle.median(day))


if __name__ == "__main__":
    unittest.main(verbosity=2)
