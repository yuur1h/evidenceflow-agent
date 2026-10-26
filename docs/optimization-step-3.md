# 第三步：引用溯源核验、调度修复与评测集

## 引用溯源核验（`deep_research/citation_check.py`）

第二步的证据门槛只检查 URL 格式。worker 让模型自行填写 `evidence.url`，模型编造一个格式正确的链接即可通过所有门槛。本步把每条引用绑定到该节点实际发生的工具调用上：

| 字段 | 取值 | 含义 |
| --- | --- | --- |
| `provenance` | `tool` / `unverified` | URL 是否出现在搜索结果、抓取页面或 Wikipedia 正文中（比较时忽略协议、`www.`、末尾斜杠和锚点） |
| `support` | `supported` / `weak` / `unsupported` | 陈述与该 URL 处观察到的文本的词元重叠率（≥0.5 / ≥0.2 / 其余）；中文按字二元组计算 |
| `support_score` | 0–1 | 重叠率原始值 |
| `support_judge` / `support_reason` | `llm` | 仅当模型复判了 `weak` 条目时出现 |

处理规则：

- `unverified` 条目不会删除，但降级为 `evidence_type=inference`、`confidence=low`，并且 `usable_evidence()` 返回 False。覆盖判定、依赖解锁、提前停止和置信度上限都只看通过核验的证据。
- `weak` 条目在 `CITATION_LLM_CHECK=1`（默认）时交给模型复判一次，每个节点最多 8 条、一次调用；模型失败时保留规则标签。`unclear` 不改变标签。
- worker 的降级证据（直接取自工具结果）天然是 `tool`/`supported`。
- 没有任何工具输出时，所有带 URL 的引用都是 `unverified`。这是有意的：没有工具的运行不能产出"有来源"的结论。
- 未经标注的证据（例如测试夹具）只走结构性门槛；标注发生在 worker 内，真实运行中每条证据都会被标注。

对外可见：报告 `sources[]` 带 `provenance`/`support`；合成器上下文每条证据标注溯源与原文支持，提示模型不要引用未核验条目；coverage `notes` 汇总被拒绝的引用数；`metadata.citations` 给出各类计数。

局限：词元重叠只能证明来源文本里出现了相关词，不能证明语义蕴含；模型复判依赖片段质量（搜索摘要通常只有一两句）。`provenance=tool` 也只说明 URL 来自工具，不说明网页本身可信。

## 调度与记忆逻辑修复

- **提前停止不可达**：`_has_sufficient_final_answer` 要求覆盖完成且所有节点终止，而循环在同一条件下已先行 `break`。现在它在多跳模式下、必需维度已被干净证据覆盖、某个答案节点以高置信度的有来源证据 resolved、且仅剩可选节点 pending 时触发；被跳过的节点不再计入 `blocked_states`。
- **兜底重规划丢弃冲突**：多跳模式下 planner 的兜底重规划只保留提到候选名称的冲突，与第二步"不用候选名称过滤冲突"的规则相悖。现在所有冲突都生成 verify 节点，并以冲突文本本身作为检索词。
- **记忆条目 ID 不稳定**：`hash()` 在每个进程中带随机盐，同一条证据跨进程会重复入库。改用 SHA-1 摘要。
- **失败率忽略 partial**：`failure_rate()` 的分母现包含 partial 节点。
- 删除无调用的候选词辅助函数。

## 评测集（`evaluation/benchmark/`）

25 题：12 道多跳问答（金标答案）、8 条事实核查（金标判定）、5 个专题研究（5 条评分要点），中英文混合。`evaluation/run_benchmark.py` 逐题运行、按模式打分，并记录 `research_status`、耗时、token 和引用核验计数，结果可断点续跑。见 [评测集说明](../evaluation/benchmark/README.md)。

本次只建立了评测流程，没有真实模型和搜索服务的运行结果，准确率、耗时和成本仍未测得。

## 验证范围

离线：`./dev.sh test`（92 个测试 + smoke 检查），覆盖 URL 归一化、三种工具来源的 URL 提取、伪造链接降级、无关陈述判为 unsupported、中文重叠、模型复判只处理 weak 条目及失败回退、worker 端到端标注、提前停止的正反例、ID 稳定性、冲突保留、评测集校验与打分续跑。

未验证：真实模型下的提前停止触发率、模型复判的准确性、评测集分数。
