# Changelog

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
