"""Scale-doubling benchmarks for long intervals and duplicate hotspots."""

from __future__ import annotations

import random
import statistics
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from temporal_quantile import INT32_MAX, INT32_MIN, Index

DAYS = 32768
SIZES = (256, 512, 1024, 2048, 4096)
QUERY_COUNT = 256
TIMING_REPEATS = 3

PutSpec = Tuple[str, int, int, int]


def make_long_interval_workload(size: int, seed: int) -> Dict[str, Any]:
    rng = random.Random(seed)
    puts: List[PutSpec] = []

    for number in range(size):
        start = rng.randrange(0, DAYS - 2048 + 1)
        length = rng.randrange(2048, DAYS - start + 1)
        value = rng.randint(INT32_MIN, INT32_MAX)
        puts.append((f"long-{number}", start, start + length, value))

    replacements: List[PutSpec] = []
    for number in range(size // 4):
        target = rng.randrange(size)
        start = rng.randrange(0, DAYS - 2048 + 1)
        length = rng.randrange(2048, DAYS - start + 1)
        value = rng.randint(INT32_MIN, INT32_MAX)
        replacements.append((f"long-{target}", start, start + length, value))

    query_days = [rng.randrange(DAYS) for _ in range(QUERY_COUNT)]
    return {
        "puts": puts,
        "replacements": replacements,
        "query_days": query_days,
        "label": "long intervals",
    }


def make_hotspot_workload(size: int, seed: int) -> Dict[str, Any]:
    rng = random.Random(seed + 7_000_000)
    puts = [(f"hot-{number}", 0, DAYS, 42) for number in range(size)]
    replacements = []
    for number in range(size):
        target = rng.randrange(size)
        replacements.append((f"hot-{target}", 0, DAYS, rng.randint(-1000, 1000)))
    query_days = [rng.randrange(DAYS) for _ in range(QUERY_COUNT)]
    return {
        "puts": puts,
        "replacements": replacements,
        "query_days": query_days,
        "label": "duplicate hotspot",
    }


def apply_modifications(
    index: Index, puts: Sequence[PutSpec], replacements: Sequence[PutSpec]
) -> None:
    for observation_id, start, end, value in puts:
        index.put(observation_id, start, end, value)
    for observation_id, start, end, value in replacements:
        index.put(observation_id, start, end, value)


def build_index(workload: Dict[str, Any]) -> Index:
    index = Index(DAYS)
    apply_modifications(index, workload["puts"], workload["replacements"])
    return index


def active_segment_nodes(index: Index) -> int:
    return sum(
        1
        for node in range(len(index.seg_left))
        if node not in index.seg_free
        and (
            index.seg_bag[node] != -1
            or index.seg_left[node] != -1
            or index.seg_right[node] != -1
        )
    )


def active_treap_nodes(index: Index) -> int:
    return len(index.treap_key) - len(index.treap_free)


def time_workload(workload: Dict[str, Any]) -> Dict[str, float]:
    modification_runs = []
    query_runs = []

    for _ in range(TIMING_REPEATS):
        index = Index(DAYS)
        started = time.perf_counter()
        apply_modifications(index, workload["puts"], workload["replacements"])
        modification_runs.append(time.perf_counter() - started)

        started = time.perf_counter()
        for day in workload["query_days"]:
            index.median(day)
        query_runs.append(time.perf_counter() - started)

    total_modifications = len(workload["puts"]) + len(workload["replacements"])

    # Rebuild and measure each phase's counters independently so medians do
    # not pay for constructing the index while being timed and counted.
    modification_index = Index(DAYS)
    apply_modifications(
        modification_index, workload["puts"], workload["replacements"]
    )
    modification_visits = (
        modification_index.segment_visits + modification_index.treap_visits
    )

    query_index = Index(DAYS)
    apply_modifications(query_index, workload["puts"], workload["replacements"])
    query_index.reset_counters()
    for day in workload["query_days"]:
        query_index.median(day)
    query_visits = query_index.segment_visits + query_index.treap_visits

    return {
        "modification_seconds": statistics.median(modification_runs),
        "query_seconds": statistics.median(query_runs),
        "visits_per_modification": modification_visits / total_modifications,
        "visits_per_query": query_visits / QUERY_COUNT,
        "active_segments": active_segment_nodes(modification_index),
        "active_treap_nodes": active_treap_nodes(modification_index),
        "modification_operations": total_modifications,
    }


def measure_peak_memory(workload: Dict[str, Any]) -> float:
    tracemalloc.start()
    index = build_index(workload)
    for day in workload["query_days"][:32]:
        index.median(day)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak / 1024.0


def run() -> str:
    rows = []
    for size in SIZES:
        for scenario, factory in (
            ("long", make_long_interval_workload),
            ("hotspot", make_hotspot_workload),
        ):
            workload = factory(size, seed=91_000 + size)
            metrics = time_workload(workload)
            metrics["peak_kib"] = measure_peak_memory(workload)
            metrics["scenario"] = scenario
            metrics["size"] = size
            rows.append(metrics)

    header = (
        "| scenario | n observations | mod ops | active segment nodes | "
        "active treap keys | peak KiB | visits/mod | visits/median | "
        "ms/mod | ms/median |"
    )
    separator = "|" + "---|" * 10
    lines = [
        "# Temporal quantile benchmark",
        "",
        f"days={DAYS}, query count per run={QUERY_COUNT}, "
        f"timing repeats={TIMING_REPEATS}; each size doubles the prior size.",
        "Peak memory is measured by tracemalloc and includes Python allocator overhead.",
        "",
        header,
        separator,
    ]

    for row in rows:
        lines.append(
            "| {scenario} | {size} | {modification_operations} | "
            "{active_segments} | {active_treap_nodes} | {peak_kib:.1f} | "
            "{visits_per_modification:.1f} | {visits_per_query:.1f} | "
            "{ms_mod:.4f} | {ms_query:.4f} |".format(
                ms_mod=1000
                * row["modification_seconds"]
                / row["modification_operations"],
                ms_query=1000 * row["query_seconds"] / QUERY_COUNT,
                **row,
            )
        )

    lines.extend(
        [
            "",
            "## Workloads",
            "",
            "- long: every interval spans at least 2,048 of 32,768 days; n/4 IDs are replaced.",
            "- hotspot: all n observations cover the full range and initially share one value; "
            "all n IDs are replaced, exercising duplicate counting and freed-node reuse.",
            "- visits count touched segment-tree and treap nodes, not observations, days, or values.",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    print(run())
