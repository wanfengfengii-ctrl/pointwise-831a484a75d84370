# Temporal quantile benchmark

days=32768, query count per run=256, timing repeats=3; each size doubles the prior size.
Peak memory is measured by tracemalloc and includes Python allocator overhead.

| scenario | n observations | mod ops | active segment nodes | active treap keys | peak KiB | visits/mod | visits/median | ms/mod | ms/median |
|---|---|---|---|---|---|---|---|---|---|
| long | 256 | 320 | 3968 | 3406 | 585.4 | 72.5 | 1101.4 | 0.0233 | 0.1240 |
| hotspot | 256 | 512 | 1 | 148 | 19.4 | 9.6 | 271.0 | 0.0032 | 0.0334 |
| long | 512 | 640 | 6418 | 6667 | 1100.4 | 77.7 | 1340.0 | 0.0237 | 0.1531 |
| hotspot | 512 | 1024 | 1 | 294 | 39.9 | 11.4 | 359.0 | 0.0037 | 0.0309 |
| long | 1024 | 1280 | 10423 | 13297 | 2018.6 | 87.1 | 1638.0 | 0.0530 | 0.2040 |
| hotspot | 1024 | 2048 | 1 | 564 | 83.0 | 12.2 | 360.0 | 0.0033 | 0.0359 |
| long | 2048 | 2560 | 15938 | 26678 | 3833.7 | 99.6 | 2081.7 | 0.0359 | 0.2413 |
| hotspot | 2048 | 4096 | 1 | 949 | 153.3 | 12.5 | 359.0 | 0.0040 | 0.0404 |
| long | 4096 | 5120 | 22944 | 53469 | 7386.9 | 112.4 | 2348.3 | 0.0566 | 0.2881 |
| hotspot | 4096 | 8192 | 1 | 1455 | 392.7 | 14.0 | 451.0 | 0.0050 | 0.0463 |

## Workloads

- long: every interval spans at least 2,048 of 32,768 days; n/4 IDs are replaced.
- hotspot: all n observations cover the full range and initially share one value; all n IDs are replaced, exercising duplicate counting and freed-node reuse.
- visits count touched segment-tree and treap nodes, not observations, days, or values.
