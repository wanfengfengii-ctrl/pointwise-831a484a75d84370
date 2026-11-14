# temporal_quantile

Python 标准库实现的动态、可更正时序区间中位数索引。所有代码仅使用标准库，单进程内存保存状态。

## 接口

```python
from temporal_quantile import Index

index = Index(days)
index.put(id, start, end, value)  # 新增或按 ID 完整替换，返回 None
index.delete(id)                  # 删除；ID 缺失时无变化，返回 None
index.median(day)                 # 空集返回 None，否则返回零起下标 floor((n-1)/2) 的值
```

约束：

- `days` 是 `1..32768` 的非布尔整数。
- `id` 是非空字符串。
- `start/end/day` 是满足区间约束的非布尔整数，区间为半开 `[start, end)`。
- `value` 是有符号 32 位整数，即 `-2^31..2^31-1`。
- 非法参数抛出 `ValueError`，并在修改前完成参数检查，状态不变。

## 数据结构与索引不变量

1. **动态二进制段树**
   - 逻辑域是 `[0, days)`，不建立逐日数组。
   - 每个区间通过区间分解写入 `O(log D)` 个规范段，其中 `D=days`。
   - 只创建实际需要的段节点；空段节点会被释放并进入空闲列表复用。
   - 对某 ID 的替换先移除旧区间，再加入新区间；删除后不保留历史节点。

2. **随机 Treap 多重集合**
   - 每个活动段节点上有一个 Treap，保存落到该规范段的 value。
   - 相同 value 只占一个 Treap 键节点，通过 `count` 分别计数，因此重复热点不会按重复次数扩张键空间。
   - Treap 维护子树多重集合大小，支持插入、删除、统计严格小于某边界的元素数。
   - Treap 节点删除后进入空闲列表复用，存储不随历史修改次数累积。

3. **点查询**
   - 从段树根沿单个 day 的路径下行，最多访问 `O(log D)` 个段节点。
   - 路径上每个 Treap 统计小于候选值的元素数。
   - 在有符号 32 位整数域上做固定 32 次二分，找到满足“小于等于该值的元素数大于目标下标”的最小值，即下中位数。
   - 不枚举当天全部值，也不扫描全部观测或覆盖天数。

## 复杂度

令 `D=days`，当前活动观测数为 `m`，单个 Treap 的不同键数上界为 `m`。期望复杂度基于随机 Treap 优先级：

| 操作 | 时间复杂度 | 说明 |
|---|---:|---|
| `put` | `O(log D log m)` 期望 | 旧区间移除和新区间插入，各分解为 `O(log D)` 个 Treap 更新 |
| `delete` | `O(log D log m)` 期望 | 只移除该 ID 的当前区间 |
| `median` | `O(log D log m)` 期望 | 深度 `O(log D)`；每级 32 次有界域二分，每次 Treap 计数 `O(log m)` |

空间复杂度为：

- 段节点：`O(m log D)`，且只包含当前活动覆盖所需要的节点。
- Treap 键：不超过 `O(m log D)`；全范围重复值共享键时可显著更低。
- ID 记录：`O(m)`。
- 总空间：`O(m log D)`，不按 `D` 逐日展开，也不随历史修改次数增长。

这里的 `log D` 在本题最大 `D=32768` 时不超过 15。

## 核验

`test_temporal_quantile.py` 包含：

- 题目给定更正示例。
- 半开区间边界、重复值、有符号 32 位边界值、替换、删除、缺失 ID。
- 非法参数及状态不变检查。
- 与独立朴素实现的 30 个随机种子对照；朴素实现保存区间并在查询时排序当天所有值。
- 同一 ID 反复替换后的活动节点检查，验证空闲节点复用、无历史累积。

运行：

```bash
python3 -I /workspace/test_temporal_quantile.py
```

## 演示

```bash
python3 -I /workspace/temporal_quantile.py demo
```

输出展示：`a=[0,3),10`、`b=[1,2),2` 时 `median(1)=2`；把 `b` 完整替换为 `[2,3),20` 后，`median(1)=median(2)=10`。

## 倍增实测

基准脚本：

```bash
python3 -I /workspace/benchmark_temporal_quantile.py
```

最近一次完整结果已保存到 `BENCHMARK.md`。规模 `n` 为 256、512、1024、2048、4096，每次翻倍：

- **长区间**：每个区间至少覆盖 32768 天中的 2048 天，并替换 `n/4` 个 ID。
- **重复热点**：`n` 个观测均覆盖完整范围且初始值相同，再替换全部 ID，检查重复计数和节点复用。
- 访问量统计实际触达的段树/Treap 节点数，不按观测总数、天数或当天值数扫描。
- 峰值内存由 `tracemalloc` 实测，包含 Python 分配器开销。

## Pointwise 运行入口

`.pointwise-runtime.json` 提供标准库语法编译、完整核验和演示命令。
