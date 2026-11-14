"""Dynamic temporal median index implemented with the Python standard library.

The index combines a dynamic binary interval segment tree with randomized
treap multisets.  No per-day arrays are kept, and freed nodes are reused so
storage does not grow with the number of historical replacements.
"""

from __future__ import annotations

import random
import sys
from typing import Any, Dict, List, Tuple

INT32_MIN = -(1 << 31)
INT32_MAX = (1 << 31) - 1


class Index:
    """Support interval corrections and point-in-time lower-median queries."""

    def __init__(self, days: int) -> None:
        self._require_int(days, "days", 1, 32768)
        self.days = days

        # Live observations: id -> (start, end, value).
        self._observations: Dict[str, Tuple[int, int, int]] = {}
        self._root = -1

        # Dynamic segment-tree nodes.  Free slots are reused.
        self.seg_left: List[int] = []
        self.seg_right: List[int] = []
        self.seg_bag: List[int] = []
        self.seg_free: List[int] = []

        # Randomized treap nodes.  Equal values share one node with a count.
        self.treap_left: List[int] = []
        self.treap_right: List[int] = []
        self.treap_key: List[int] = []
        self.treap_count: List[int] = []
        self.treap_size: List[int] = []
        self.treap_priority: List[int] = []
        self.treap_free: List[int] = []

        # Instrumentation for measured access-volume benchmarks.
        self.put_calls = 0
        self.delete_calls = 0
        self.median_calls = 0
        self.segment_visits = 0
        self.treap_visits = 0

    @staticmethod
    def _require_int(value: Any, name: str, low: int, high: int) -> None:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{name} must be an integer without bool subtype")
        if value < low or value > high:
            raise ValueError(f"{name} must satisfy {low} <= {name} <= {high}")

    def _require_id(self, observation_id: Any) -> None:
        if not isinstance(observation_id, str) or observation_id == "":
            raise ValueError("id must be a non-empty string")

    def reset_counters(self) -> None:
        """Reset operation and internal node-visit counters."""
        self.put_calls = 0
        self.delete_calls = 0
        self.median_calls = 0
        self.segment_visits = 0
        self.treap_visits = 0

    # ------------------------------------------------------------------
    # Treap multiset
    # ------------------------------------------------------------------
    def _treap_size(self, node: int) -> int:
        return 0 if node == -1 else self.treap_size[node]

    def _update_treap(self, node: int) -> None:
        self.treap_size[node] = (
            self.treap_count[node]
            + self._treap_size(self.treap_left[node])
            + self._treap_size(self.treap_right[node])
        )

    def _new_treap_node(self, key: int) -> int:
        if self.treap_free:
            node = self.treap_free.pop()
            self.treap_left[node] = -1
            self.treap_right[node] = -1
        else:
            node = len(self.treap_key)
            self.treap_left.append(-1)
            self.treap_right.append(-1)
            self.treap_key.append(key)
            self.treap_count.append(0)
            self.treap_size.append(0)
            self.treap_priority.append(random.getrandbits(64))

        self.treap_key[node] = key
        self.treap_count[node] = 1
        self.treap_size[node] = 1
        return node

    def _rotate_right(self, root: int) -> int:
        child = self.treap_left[root]
        self.treap_left[root] = self.treap_right[child]
        self.treap_right[child] = root
        self._update_treap(root)
        self._update_treap(child)
        return child

    def _rotate_left(self, root: int) -> int:
        child = self.treap_right[root]
        self.treap_right[root] = self.treap_left[child]
        self.treap_left[child] = root
        self._update_treap(root)
        self._update_treap(child)
        return child

    def _treap_insert(self, root: int, key: int) -> int:
        if root == -1:
            return self._new_treap_node(key)

        self.treap_visits += 1
        if key == self.treap_key[root]:
            self.treap_count[root] += 1
        elif key < self.treap_key[root]:
            self.treap_left[root] = self._treap_insert(
                self.treap_left[root], key
            )
            if self.treap_priority[self.treap_left[root]] > self.treap_priority[root]:
                root = self._rotate_right(root)
        else:
            self.treap_right[root] = self._treap_insert(
                self.treap_right[root], key
            )
            if self.treap_priority[self.treap_right[root]] > self.treap_priority[root]:
                root = self._rotate_left(root)

        self._update_treap(root)
        return root

    def _merge_treaps(self, left: int, right: int) -> int:
        if left == -1:
            return right
        if right == -1:
            return left

        self.treap_visits += 1
        if self.treap_priority[left] > self.treap_priority[right]:
            self.treap_right[left] = self._merge_treaps(
                self.treap_right[left], right
            )
            self._update_treap(left)
            return left

        self.treap_left[right] = self._merge_treaps(
            left, self.treap_left[right]
        )
        self._update_treap(right)
        return right

    def _treap_delete(self, root: int, key: int) -> int:
        if root == -1:
            return -1

        self.treap_visits += 1
        if key == self.treap_key[root]:
            self.treap_count[root] -= 1
            if self.treap_count[root] > 0:
                self._update_treap(root)
                return root

            merged = self._merge_treaps(
                self.treap_left[root], self.treap_right[root]
            )
            self.treap_free.append(root)
            return merged

        if key < self.treap_key[root]:
            self.treap_left[root] = self._treap_delete(
                self.treap_left[root], key
            )
        else:
            self.treap_right[root] = self._treap_delete(
                self.treap_right[root], key
            )

        self._update_treap(root)
        return root

    def _treap_count_less(self, root: int, boundary: int) -> int:
        """Count multiset entries whose key is strictly less than boundary."""
        result = 0
        while root != -1:
            self.treap_visits += 1
            key = self.treap_key[root]
            if boundary <= key:
                root = self.treap_left[root]
            else:
                result += self._treap_size(self.treap_left[root])
                result += self.treap_count[root]
                root = self.treap_right[root]
        return result

    # ------------------------------------------------------------------
    # Dynamic segment tree
    # ------------------------------------------------------------------
    def _new_segment_node(self) -> int:
        if self.seg_free:
            node = self.seg_free.pop()
            self.seg_left[node] = -1
            self.seg_right[node] = -1
            self.seg_bag[node] = -1
        else:
            node = len(self.seg_left)
            self.seg_left.append(-1)
            self.seg_right.append(-1)
            self.seg_bag.append(-1)
        return node

    def _free_segment_node(self, node: int) -> None:
        self.seg_left[node] = -1
        self.seg_right[node] = -1
        self.seg_bag[node] = -1
        self.seg_free.append(node)

    def _segment_add(
        self,
        node: int,
        lo: int,
        hi: int,
        query_left: int,
        query_right: int,
        value: int,
    ) -> int:
        self.segment_visits += 1
        if node == -1:
            node = self._new_segment_node()

        if query_left <= lo and hi <= query_right:
            self.seg_bag[node] = self._treap_insert(
                self.seg_bag[node], value
            )
            return node

        middle = (lo + hi) // 2
        if query_left < middle:
            self.seg_left[node] = self._segment_add(
                self.seg_left[node],
                lo,
                middle,
                query_left,
                query_right,
                value,
            )
        if query_right > middle:
            self.seg_right[node] = self._segment_add(
                self.seg_right[node],
                middle,
                hi,
                query_left,
                query_right,
                value,
            )
        return node

    def _segment_remove(
        self,
        node: int,
        lo: int,
        hi: int,
        query_left: int,
        query_right: int,
        value: int,
    ) -> int:
        self.segment_visits += 1
        if node == -1:
            return -1

        if query_left <= lo and hi <= query_right:
            self.seg_bag[node] = self._treap_delete(
                self.seg_bag[node], value
            )
            if self.seg_bag[node] != -1:
                return node
            # An empty bag can still be an internal node with live children.
        else:
            middle = (lo + hi) // 2
            if query_left < middle:
                self.seg_left[node] = self._segment_remove(
                    self.seg_left[node],
                    lo,
                    middle,
                    query_left,
                    query_right,
                    value,
                )
            if query_right > middle:
                self.seg_right[node] = self._segment_remove(
                    self.seg_right[node],
                    middle,
                    hi,
                    query_left,
                    query_right,
                    value,
                )

        if (
            self.seg_bag[node] == -1
            and self.seg_left[node] == -1
            and self.seg_right[node] == -1
        ):
            self._free_segment_node(node)
            return -1
        return node

    def _count_at_day(self, day: int) -> int:
        node = self._root
        lo = 0
        hi = self.days
        total = 0
        while node != -1:
            self.segment_visits += 1
            total += self._treap_size(self.seg_bag[node])
            if hi - lo == 1:
                break
            middle = (lo + hi) // 2
            if day < middle:
                node = self.seg_left[node]
                hi = middle
            else:
                node = self.seg_right[node]
                lo = middle
        return total

    def _count_less_at_day(self, day: int, boundary: int) -> int:
        node = self._root
        lo = 0
        hi = self.days
        total = 0
        while node != -1:
            self.segment_visits += 1
            total += self._treap_count_less(self.seg_bag[node], boundary)
            if hi - lo == 1:
                break
            middle = (lo + hi) // 2
            if day < middle:
                node = self.seg_left[node]
                hi = middle
            else:
                node = self.seg_right[node]
                lo = middle
        return total

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def put(self, observation_id: str, start: int, end: int, value: int) -> None:
        self._require_id(observation_id)
        self._require_int(start, "start", 0, self.days - 1)
        self._require_int(end, "end", 1, self.days)
        if start >= end:
            raise ValueError("require start < end")
        self._require_int(value, "value", INT32_MIN, INT32_MAX)

        if observation_id in self._observations:
            old_start, old_end, old_value = self._observations[observation_id]
            self._root = self._segment_remove(
                self._root, 0, self.days, old_start, old_end, old_value
            )

        self._observations[observation_id] = (start, end, value)
        self._root = self._segment_add(
            self._root, 0, self.days, start, end, value
        )
        self.put_calls += 1
        return None

    def delete(self, observation_id: str) -> None:
        self._require_id(observation_id)
        old = self._observations.get(observation_id)
        if old is None:
            return None

        start, end, value = old
        self._root = self._segment_remove(
            self._root, 0, self.days, start, end, value
        )
        del self._observations[observation_id]
        self.delete_calls += 1
        return None

    def median(self, day: int) -> int | None:
        self._require_int(day, "day", 0, self.days - 1)
        self.median_calls += 1

        count = self._count_at_day(day)
        if count == 0:
            return None

        target = (count - 1) // 2
        low = INT32_MIN
        high = INT32_MAX

        # Signed 32-bit domain has a fixed 32-iteration binary search.
        while low < high:
            middle = low + (high - low) // 2
            less_or_equal = self._count_less_at_day(day, middle + 1)
            if less_or_equal > target:
                high = middle
            else:
                low = middle + 1
        return low


def _demo() -> None:
    index = Index(3)
    index.put("a", 0, 3, 10)
    index.put("b", 1, 2, 2)
    print("put a=[0,3), value=10")
    print("put b=[1,2), value=2")
    print(f"median(1) = {index.median(1)}  # expected 2")

    index.put("b", 2, 3, 20)
    print("replace b with [2,3), value=20")
    print(f"median(1) = {index.median(1)}  # expected 10")
    print(f"median(2) = {index.median(2)}  # expected 10")


def main(argv: List[str]) -> int:
    if len(argv) == 1 and argv[0] == "demo":
        _demo()
        return 0
    if len(argv) == 1 and argv[0] in ("-h", "--help"):
        print("usage: python3 temporal_quantile.py demo")
        return 0
    print("usage: python3 temporal_quantile.py demo", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
