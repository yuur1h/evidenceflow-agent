# 第四步：多证据联合评估（answered 状态）

## 问题

第三步的 25 题评测里 21 题答对，但只有 10 题到达 `complete`，其余 15 题全部以"证据不足"返回：
多跳问答的答案被替换成"证据不足，暂无法确定答案"（正确答案只留在 `candidate_answer`），
事实核查和专题研究的报告前面挂上"研究未完成"的横幅。原因是收尾只看覆盖检查：
只要有一个 checklist 标签没有干净节点、一个节点 partial/failed、一个问题没查清，答案就整体作废。
覆盖检查回答的是"计划里的每一步都走完了吗"，不是"已收集的证据是否足以支持一个答案"。

## 方案

新增 `deep_research/confidence.py`，在综合之后、收尾之前对证据做一次联合评估，与覆盖检查解耦：

| 因子 | 含义 |
| --- | --- |
| agreement | 各节点 `candidate_answer`（含别名）和综合答案聚类投票，共识簇的权重占比。多跳模式只让计划里标为答案节点（`answer`/`final_answer` 标签或 expected_output 含 final/candidate_answer）的节点投票，中间跳的实体（如“11 名球员”）不算竞争候选 |
| independent_sources | 提及共识答案的经核验证据来自多少个不同主机（去 `www.`），3 个记满分 |
| evidence_quality | 支持证据的 置信度 × 原文支持系数（supported 1.0 / unchecked 0.85 / weak 0.5）× 一手来源系数 |
| coverage | 覆盖标签完成比例（软因子，不再是一票否决） |
| resolved_states | 产出率：resolved 节点记 1，仍为 partial 但产出了经核验证据的节点记 0.5（事实核查节点在第二步的门槛下几乎不会 resolved） |

多跳与事实核查：`0.30·agreement + 0.30·sources + 0.20·quality + 0.10·coverage + 0.10·resolved`，
一手来源 +0.05；未消解冲突按次扣固定分（多跳 −0.15、事实核查 −0.10：支持与反驳证据并存本就是核查的常态，记忆库的冲突检测又会按两两配对计数）、
强竞争候选 −0.15、每个未查清问题 −0.05（最多 −0.10）；没有任何经核验证据提及答案时分数上限 0.40。
多跳模式下竞争候选与未消解冲突同时出现时不交付，除非交叉核验判为 consistent。专题研究没有单一答案，按覆盖率、解决率、证据质量和来源数打分。

分级：≥0.70 高，≥0.45 中，否则低。**可交付**要求中/高，且至少 2 个独立来源
（或 1 个一手来源且无未查清问题、无冲突、质量 ≥ 0.8）。事实核查判定为"证据不足"时永不交付。

交叉核验：覆盖未完全且有候选答案时，再用一次小规模模型调用（约 1–2k token）把候选答案与最多 12 条最相关证据放在一起，
判 `consistent / contradicted / insufficient`，分别 +0.20·置信度 / −0.30 / −0.10。这一步是启发式打分做不到的“把证据放在一起读”，所以权重给得比单个因子高。`contradicted` 直接取消交付。
`EVIDENCEFLOW_CROSS_CHECK=0` 可关闭。模型调用失败只记录，不影响结果。

收尾规则：

| 覆盖完成 | 联合评估可交付 | `research_status` | 答案 |
| --- | --- | --- | --- |
| 是 | — | `complete` | 原样 |
| 否 | 是 | `answered` | 保留答案；`confidence` 改为评估等级；`uncertainty` 追加"结论可信度…由 N 个独立来源…待补充：…" |
| 否 | 否 | `partial` | 与之前相同：答案替换为"证据不足"，候选留在 `candidate_answer`，并附联合评估理由 |

`metadata.answer_assessment` 带 score、level、factors、reasons、competing、cross_check，前端在结果横幅里展示理由；
`evaluation/run_benchmark.py` 的汇总新增 `answered_rate`（complete 或 answered 的比例）。

## 不改变的事

- 证据准入规则不变：`unverified` 链接、`unsupported` 陈述仍不计入任何因子。
- 早停规则不变，仍只在多跳模式、全部标签干净覆盖时触发。
- 单一来源的答案不会因为"看起来对"就交付；这是有意保留的保守项。
- 置信度分数是可解释的启发式评分，不是校准后的正确概率。

## 验证

离线测试 `tests/test_confidence.py`：等价答案匹配（大小写、冠词、缩写、千分位、中英混排）、多来源一致可交付、
单来源加未查清问题不可交付、竞争候选与冲突降分、未核验证据不计、事实核查判定抽取与"证据不足"不交付、
专题研究证据量门槛、交叉核验相悖阻断交付与失败仅记录、以及 runner 端到端的 answered / partial 两条路径。

在线验证见 CHANGELOG 的 Validation 小节。
