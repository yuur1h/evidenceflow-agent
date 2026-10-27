# Benchmark results

Scores are LLM-judged by the same model that ran the research; compare runs, do not read them as absolute accuracy.

## 2026-10-27 — DeepSeek `deepseek-chat` + Serper, no Jina, `--max-adversarial-rounds 0 --concurrency 2`

Code: branch `feat/deepseek-provider` at the "docs: record first live DeepSeek trial" commit. Raw rows: `outputs/benchmark/deepseek-full/` (git-ignored).

| Mode | n | Correct | `complete` | Mean elapsed | Mean tokens | Median tokens | Citations supported/total |
| --- | --- | --- | --- | --- | --- | --- | --- |
| multi_hop | 12 | 9/12 (75%) | 9/12 | 51 s | 79k | 45k | 324/334 |
| fact_check | 8 | 8/8 (100%) | 1/8 | 158 s | 308k | 234k | 817/850 |
| research | 5 | 4/5 (rubric mean 0.80) | 0/5 | 144 s | 719k* | 257k | 207/220 |
| **overall** | 25 | 21/25 (84%) | 10/25 | 104 s | 280k | — | 1348/1404 |

Total: 7.0M tokens, 2,597 s summed item time. rs-03 and rs-05 first failed with `402 Insufficient Balance` and were re-run after a top-up (they are the only rows from a second process).

\* rs-03 and rs-05 cost 1.32M and 1.69M tokens — 5× any other item. The runtime log showed single replanner calls with 240k–630k prompt tokens: `_replan` was passing every resolved state's full `tool_calls` (raw search/scrape text) to the model. Fixed in the next commit (`WorkerResult.compact_dict()`); the three earlier research items (169k–257k) were already affected to a smaller degree, so research-mode cost figures above are an upper bound.

### multi_hop

Nine answers matched gold. The three misses (mh-03 Hampshire, mh-05 Sodium, mh-09 John F. Kennedy) all ended `partial` after exhausting both replans and returned "证据不足" rather than a wrong entity; mh-03 had been answered correctly in the preceding 3-item trial, so the miss is run-to-run variance in planning/search, not a fixed defect. Partial runs cost 2.5–5× a completed one (112k–224k tokens vs a 45k median).

### fact_check

Every verdict was right, but 7/8 runs never reached `complete`, burned both replans and cost 5× a multi-hop item (fc-05 718k tokens, fc-08 579k). The gate rules from step 2 make this structural: a `counter_evidence` node for a true claim (or `supporting_evidence` for a false one) finds nothing and is kept `partial` by design ("没找到不等于证明"), and `verdict` nodes carry no source-linked evidence of their own, so coverage can never close. The replans then re-run the same searches.

### research

Rubric coverage was 4/5, 5/5, 5/5, 4/5, 2/5. Missed points: the sodium-ion low-temperature advantage (rs-01), the WHO 2021 guideline value (rs-04), and for the Transformer-vs-RNN item (rs-05) the quadratic attention cost, positional encoding and data/compute demands — the report stayed at advantages and never reached limitations. All five ran to `partial` for the same structural reason as fact_check: `synthesis` and `limitations` nodes have no URL-backed evidence.

### What this run changes next

1. Let a `counter_evidence` / `supporting_evidence` node resolve as "searched, none found" with its queries recorded, instead of `partial`, and let `verdict` / `synthesis` / `limitations` nodes be covered by the evidence of the nodes they depend on. Needs a live re-run to confirm cost drops.
2. Cap replans when a replan adds no new usable evidence; fc-05 ran 288 searches for a claim settled in the first round.
3. Re-run the research items after the compact-payload fix to get real cost numbers for that mode.
