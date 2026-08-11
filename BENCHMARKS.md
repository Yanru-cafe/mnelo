# mnelo BENCHMARKS

[8/11 P3-benchmarks] 公开 benchmark 数字 + 复现命令。任何人都能跑出来。

> "Anyone can reproduce" — mem0 memory-benchmarks 模式

## TL;DR

```bash
# 1. 把代码 + 依赖装好
git clone https://github.com/chinesewebman/mnelo.git
cd mnelo
pip install -r requirements.txt

# 2. 跑 latency benchmark（默认 10k chunks / 100 queries）
python -m benchmarks latency

# 3. 跑 LoCoMo 风格 smoke
python -m benchmarks locomo
```

## 1. Latency benchmark

`python -m benchmarks latency` — 召回延迟基准（p50 / p95 / p99 / min / max / mean / stdev）。

| 数字 | 含义 | 复现命令 |
|---|---|---|
| **p50 = 18 ms @ 5k vectors** | 5k 向量下召回中位数延迟 | `python -m benchmarks latency --chunks 5000` |
| p50 ≈ 12.5 ms @ 10k vectors | 10k 向量下召回中位数延迟 | `python -m benchmarks latency --chunks 10000` |
| p95 / p99 / min / max | 全套分位数 | `python -m benchmarks latency --json out.json` |

### 工作流

1. Seed N 合成 chunks（10k 默认；最小 100）
2. Warmup 5 queries（让 embedder + usearch 缓存热起来）
3. 跑 K measured queries（100 默认；最小 10）
4. 测每个 lane 的 latency + total
5. 清理 seed 数据（`source` 前缀 `benchmark_round15:`，幂等）

**复用理由**：README hero 数字 "p50=18ms @ 5k vectors" 用同一脚本复跑验证。

### 退出码

- 0 = success
- 1 = DB error / seed failure / 参数非法（`--chunks < 100` / `--queries < 10`）

## 2. LoCoMo benchmark (smoke)

`python -m benchmarks locomo` — LoCoMo 风格多场景召回质量 + 延迟 smoke test。

| 指标 | 含义 |
|---|---|
| coverage per scenario | 每个 scenario 的 query 召回 hit 命中主题词的比例 |
| mean coverage | 全部 scenario 的平均 coverage |
| latency p50 / mean | 同一批 query 的召回延迟 |

### 场景

内置 3 个 scenario（光伏装机 / 美联储利率 / 比亚迪销量），每个 3 个 chunk + 3 个 query。完整 LoCoMo 10-conversation dataset (50MB+) 暂未接入，留作 P3 之后。

### 完整 LoCoMo 数据集延后接入

完整 LoCoMo benchmark 数据集接入需要：
1. 拉取 [snap-stanford/locomo](https://github.com/snap-stanford/locomo) 10-conversation dataset
2. 写 mnelo graph-aware scorer（基于 entity / relation 命中，而不是粗糙的 keyword 覆盖）
3. CI 里跑全量 dataset 评分

这部分**不在 P3 范围内**，是因为：
- 50MB+ 数据集会拖慢 CI（latency benchmark 默认 90s，加 locomo 要 5+ min）
- mnelo 真正的质量优势（graph relation）需要专门 scorer，现在 benchmark 只看 keyword
- single metric 没法区分 mnelo (graph) vs naive vector store

如果用户要严格对齐 mem0 时间推理 blog 的 LoCoMo +9.1 pts（recency-aware），需要等 P3 (write-time temporal signature) 落地后再做。

## 3. 命名约定

| name | 用途 | 入口 |
|---|---|---|
| `latency` | 纯延迟 / 吞吐 | `python -m benchmarks latency` |
| `locomo` | 召回质量 + 延迟 smoke | `python -m benchmarks locomo` |
| `list` | 列出所有 benchmark | `python -m benchmarks --list` |

新增 benchmark 流程：
1. 在 `benchmarks/<name>.py` 里写 `build_parser()` + `main(argv)` + `run_<name>(args)`
2. 在 `benchmarks/__main__.py` 的 `BENCHMARKS` 注册表里登记一行
3. （可选）更新本 BENCHMARKS.md + README

## 4. 历史与边界

- 7/19 v0.5.5 起 `scripts/benchmark.py` 是 latency benchmark 唯一入口
- 8/11 P3 任务卡要求把入口提升到 `python -m benchmarks <name>`（对标 mem0）
- `scripts/benchmark.py` 保留为薄壳，向后兼容 `tests/test_benchmark_round15.py`

**与 `docs/COMPARISON.md`（横向对比表）的关系**：本档是「mnelo 自己的 benchmark num + 复现命令」；COMPARISON.md 是「mnelo vs Mem0 / Letta / Zep / Cognee 对比表」。两文件**不重复**。

**与 `docs/research/mem0-comparison.md`（深度借鉴研究）的关系**：本档是 P3 (#6) 落地结果；COMPARISON.md 是落地方案分析。两者互补。
