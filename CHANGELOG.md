# Changelog

## Unreleased

### Added
- `LLM_PROVIDER=deepseek` using DeepSeek's OpenAI-compatible endpoint (`DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`, optional `DEEPSEEK_BASE_URL`). Qwen thinking flags are not forwarded to it.

### Fixed (from the first live run)
- Worker open_questions are scoped to the node; questions that restate another planned state are deferred to followups instead of marking the node partial.
- Memory conflict cues match whole words ("no" no longer fires on "North"); a clean verify state dismisses the memory conflict it was planned for.
- Citations re-cited from earlier states are verified (`provenance=prior_state`); mixed-script claims score on Latin tokens; unsupported tool-backed items are re-judged by the model.
- Planner checklist entries like "answer: ..." are reduced to the tag the states carry.

### Validation
- DeepSeek `deepseek-chat` + Serper, 3 multi-hop items (mh-01..03): 0/3 correct before these fixes, 3/3 correct and `complete` after; mean tokens 85.9k -> 41.8k, mean elapsed 55.6s -> 33.4s, supported citations 4/67 -> 43/47.
- Full 25-item benchmark (same setup): multi_hop 9/12, fact_check 8/8, research 3/5 scored (rubric 0.87 mean); overall 20/25, 3.99M tokens. Only 10/25 reached `complete`: fact_check and research cannot close coverage under the step-2 gates (absence-of-counter-evidence and synthesis nodes stay partial) and each burns both replans. Two research items failed on account balance. Details in `evaluation/benchmark/RESULTS.md`.

## 0.3.0 — 2026-10-27

### Added
- Citation provenance and support checks: every evidence URL must appear in the state's tool output, and claims are scored against the text seen there; unverified or unsupported items are downgraded and excluded from coverage. Weak items are re-judged by the model (`CITATION_LLM_CHECK`).
- `metadata.citations` counts and provenance/support fields on report sources.
- A 25-item benchmark set across the three task modes and `evaluation/run_benchmark.py` with per-mode scoring, cost fields and resumable output.
- uv project (`pyproject.toml`, `uv.lock`), `dev.sh`, `start-local.sh` and a GitHub Actions workflow running the offline suite.

### Fixed
- Multi-hop early stop was unreachable; it now fires only when required coverage is met by verified evidence and only optional work remains.
- Fallback replans dropped conflicts that did not mention a candidate name; every conflict now gets a verify state.
- Memory entry IDs used the process-salted `hash()`; they use a stable digest.
- `failure_rate()` counts partial states.

### Validation and limitations
- 92 offline tests and smoke checks pass. No live model or search run was performed in this release; benchmark scores, cost and the accuracy of model-based citation re-judging remain unmeasured.
- Lexical support is a proxy, not entailment; `provenance=tool` does not assess source reliability.

## 0.2.0 — 2026-09-16

### Added
- Explicit multi-hop question answering, fact checking, and research modes in the API and UI.
- OpenAI Responses adapter alongside DashScope Chat Completions, isolated provider credentials, usage accounting, and client cleanup.
- Optional `QWEN_ENABLE_THINKING` setting for hybrid thinking models.
- Offline regression tests, smoke checks, and a Windows startup script.

### Fixed
- SSE streams now terminate with an error event when the background research task fails.
- Incomplete evidence no longer counts as successful research: partial states block dependent work, coverage includes source/date requirements, and retries respect the replan budget.
- Incomplete final answers disclose evidence gaps and have capped confidence.

### Validation and limitations
- Offline regression suite and smoke checks pass; these do not measure research accuracy.
- A live `qwen-max` connectivity request succeeded. A bounded Wikipedia research run completed with `partial` status because candidate lookup did not yield usable source evidence.
- The embedding model downloaded and loaded successfully. Semantic retrieval quality has not been evaluated.
- Live requests to `qwen3.8-27b`, `qwen3.7-flash-2026-07-15`, and `qwen3.8-flash` were blocked by `Arrearage`; their live behavior remains unverified.
- OpenAI behavior is tested with a mocked SDK transport, not a live OpenAI key.
- Citation entailment checks, calibrated confidence, Wikipedia candidate follow-up, and benchmark evaluation remain outstanding.

## Versioning

`VERSION` records the release version. Update it and this changelog together. Use patch versions for fixes, minor versions for compatible features, and major versions for breaking API changes. Keep commits focused and retain upstream history; do not commit credentials, local databases, logs, or virtual environments.
