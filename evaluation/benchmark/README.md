# Benchmark set

25 items across the three task modes, written to be verifiable from widely available sources.
It measures whether the pipeline reaches a defensible, evidence-backed result; it is not a
leaderboard-grade dataset.

| Mode | Items | Gold | Metric |
| --- | --- | --- | --- |
| `multi_hop` | 12 | `answer` | LLM judge: same object as gold (`evaluation/llm_judge.py`) |
| `fact_check` | 8 | `verdict` (`supported` / `refuted`) | LLM extracts the report's verdict; exact match |
| `research` | 5 | `rubric` (5 points) | LLM judge marks each rubric point covered or not; mean coverage |

Every row also records `research_status`, `coverage_complete`, elapsed seconds, token usage and
citation-check counts, so cost and completeness can be compared across configurations.

```bash
# full run with the provider in .env
.venv/bin/python -m evaluation.run_benchmark --output outputs/benchmark/baseline

# one mode, first 3 items, no adversarial review
.venv/bin/python -m evaluation.run_benchmark --modes multi_hop --limit 3 --max-adversarial-rounds 0
```

Results go to `<output>/results.jsonl` and `<output>/summary.json`; rerunning with the same
`--output` skips items already present unless `--no-resume` is given.
