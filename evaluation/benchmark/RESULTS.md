# Benchmark results

Scores are LLM-judged by the same model that ran the research; compare runs, do not read them as absolute accuracy.

## 2026-10-27 — DeepSeek `deepseek-chat` + Serper, no Jina, `--max-adversarial-rounds 0 --concurrency 2`

Code: branch `feat/deepseek-provider` at the "docs: record first live DeepSeek trial" commit. Raw rows: `outputs/benchmark/deepseek-full/` (git-ignored).

| Mode | n | Correct | `complete` | Mean elapsed | Mean tokens | Median tokens | Citations supported/total |
| --- | --- | --- | --- | --- | --- | --- | --- |
| multi_hop | 12 | 9/12 (75%) | 9/12 | 51 s | 79k | 45k | 324/334 |
| fact_check | 8 | 8/8 (100%) | 1/8 | 158 s | 308k | 234k | 817/850 |
| research | 5 | 3/5 scored, rubric mean 0.87 of those | 0/5 | 83 s | 193k | 169k | 136/149 |
| **overall** | 25 | 20/25 | 10/25 | 92 s | 173k | — | 1277/1333 |

Total: 3.99M tokens, 2,293 s summed item time. Two research items (rs-03, rs-05) failed with `402 Insufficient Balance` when the account ran out; they are counted as incorrect above and have no cost figures.

### multi_hop

Nine answers matched gold. The three misses (mh-03 Hampshire, mh-05 Sodium, mh-09 John F. Kennedy) all ended `partial` after exhausting both replans and returned "证据不足" rather than a wrong entity; mh-03 had been answered correctly in the preceding 3-item trial, so the miss is run-to-run variance in planning/search, not a fixed defect. Partial runs cost 2.5–5× a completed one (112k–224k tokens vs a 45k median).

### fact_check

Every verdict was right, but 7/8 runs never reached `complete`, burned both replans and cost 5× a multi-hop item (fc-05 718k tokens, fc-08 579k). The gate rules from step 2 make this structural: a `counter_evidence` node for a true claim (or `supporting_evidence` for a false one) finds nothing and is kept `partial` by design ("没找到不等于证明"), and `verdict` nodes carry no source-linked evidence of their own, so coverage can never close. The replans then re-run the same searches.

### research

Rubric coverage was 4/5, 5/5, 4/5 on the three items that ran; the missed points were the sodium-ion low-temperature advantage (rs-01) and the WHO 2021 guideline value (rs-04). All ran to `partial` for the same structural reason as fact_check: `synthesis` and `limitations` nodes have no URL-backed evidence.

### What this run changes next

1. Let a `counter_evidence` / `supporting_evidence` node resolve as "searched, none found" with its queries recorded, instead of `partial`, and let `verdict` / `synthesis` / `limitations` nodes be covered by the evidence of the nodes they depend on. Needs a live re-run to confirm cost drops.
2. Cap replans when a replan adds no new usable evidence; fc-05 ran 288 searches for a claim settled in the first round.
3. Re-run rs-03 and rs-05 once the account has balance.
