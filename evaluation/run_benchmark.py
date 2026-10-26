"""Run the benchmark set through the research runner and score each mode.

Scores are LLM-judged, so they are comparable across runs with the same judge model, not
absolute truth. Cost fields (elapsed, tokens) come straight from runner metadata.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from deep_research.llm import create_llm, extract_json_object
from deep_research.runner import DeepResearchRunner
from deep_research.task_modes import TaskMode
from evaluation.llm_judge import judge_one, load_jsonl

DEFAULT_DATASET = PROJECT_ROOT / "evaluation" / "benchmark" / "benchmark.jsonl"
VERDICTS = {"supported", "refuted", "partially_supported", "insufficient_evidence"}

VERDICT_SYSTEM = (
    "你是事实核查评测员。读取一份核查报告，提取它对待核查声明的最终判定。"
    '只输出严格 JSON：{"verdict":"supported|refuted|partially_supported|insufficient_evidence"}。'
    "报告没有明确判定时输出 insufficient_evidence。"
)
RUBRIC_SYSTEM = (
    "你是研究报告评测员。给定报告和若干评分要点，逐条判断报告是否实质覆盖该要点（仅提及关键词不算）。"
    '只输出严格 JSON：{"covered":[true,false,...]}，顺序与要点一致。'
)

RunnerFactory = Callable[[dict[str, Any], Path], DeepResearchRunner]


def validate_rows(rows: list[dict[str, Any]]) -> list[str]:
    problems = []
    seen = set()
    for row in rows:
        rid = row.get("id")
        if not rid or rid in seen:
            problems.append(f"duplicate or missing id: {rid!r}")
        seen.add(rid)
        mode = row.get("mode")
        if mode not in {m.value for m in TaskMode}:
            problems.append(f"{rid}: unknown mode {mode!r}")
        if not str(row.get("question") or "").strip():
            problems.append(f"{rid}: empty question")
        if mode == "multi_hop" and not row.get("answer"):
            problems.append(f"{rid}: multi_hop needs answer")
        if mode == "fact_check" and row.get("verdict") not in {"supported", "refuted"}:
            problems.append(f"{rid}: fact_check needs verdict supported|refuted")
        if mode == "research" and not (isinstance(row.get("rubric"), list) and row["rubric"]):
            problems.append(f"{rid}: research needs a non-empty rubric")
    return problems


def default_runner_factory(args: argparse.Namespace) -> RunnerFactory:
    def build(row: dict[str, Any], memory_dir: Path) -> DeepResearchRunner:
        memory_dir.mkdir(parents=True, exist_ok=True)
        return DeepResearchRunner(
            task_mode=row["mode"],
            session_id=f"bench-{row['id']}-{int(time.time())}",
            max_concurrent=args.max_concurrent,
            max_replans=args.max_replans,
            max_adversarial_rounds=args.max_adversarial_rounds,
            memory_db_path=str(memory_dir / f"{row['id']}.db"),  # per-item memory isolation
        )
    return build


async def score_row(judge: Any, row: dict[str, Any], answer: str, report: str) -> dict[str, Any]:
    mode = row["mode"]
    if mode == "multi_hop":
        verdict = await judge_one(llm=judge, question=row["question"], gold_answer=row["answer"], predicted_answer=answer)
        return {"correct": verdict["judgement"] == "correct" and verdict["is_same_object"],
                "score": 1.0 if verdict["judgement"] == "correct" and verdict["is_same_object"] else 0.0,
                "judge": {k: v for k, v in verdict.items() if k != "raw_judge"}}
    if mode == "fact_check":
        raw = await judge.complete([{"role": "system", "content": VERDICT_SYSTEM},
                                    {"role": "user", "content": f"待核查声明：{row['question']}\n\n报告：\n{report[:6000]}"}],
                                   temperature=0.0)
        predicted = str((extract_json_object(raw) or {}).get("verdict") or "insufficient_evidence").lower()
        if predicted not in VERDICTS:
            predicted = "insufficient_evidence"
        return {"correct": predicted == row["verdict"], "score": 1.0 if predicted == row["verdict"] else 0.0,
                "judge": {"predicted_verdict": predicted}}
    points = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(row["rubric"]))
    raw = await judge.complete([{"role": "system", "content": RUBRIC_SYSTEM},
                                {"role": "user", "content": f"评分要点：\n{points}\n\n报告：\n{report[:12000]}"}],
                               temperature=0.0)
    flags = (extract_json_object(raw) or {}).get("covered") or []
    covered = [bool(flags[i]) if i < len(flags) else False for i in range(len(row["rubric"]))]
    coverage = sum(covered) / len(covered)
    return {"correct": coverage >= 0.8, "score": round(coverage, 3),
            "judge": {"covered": covered, "rubric": row["rubric"]}}


async def run_item(row: dict[str, Any], *, runner_factory: RunnerFactory, judge: Any, memory_dir: Path) -> dict[str, Any]:
    runner = runner_factory(row, memory_dir)
    started = time.monotonic()
    result = await runner.run(str(row["question"]))
    elapsed = round(time.monotonic() - started, 3)
    payload = result.to_dict()
    meta = payload.get("metadata") or {}
    report = (payload.get("report") or {}).get("content") or ""
    scored = await score_row(judge, row, payload.get("answer") or "", report)
    return {
        "id": row["id"], "mode": row["mode"], "question": row["question"],
        "gold": row.get("answer") or row.get("verdict") or row.get("rubric"),
        "answer": payload.get("answer"),
        **scored,
        "research_status": meta.get("research_status"),
        "coverage_complete": bool(meta.get("coverage_complete")),
        "failed": bool(meta.get("failed")),
        "elapsed_seconds": elapsed,
        "tokens": (meta.get("llm_usage") or {}).get("total_tokens"),
        "num_searches": meta.get("num_searches"), "num_replans": meta.get("num_replans"),
        "citations": meta.get("citations") or {},
        "confidence": (payload.get("report") or {}).get("confidence"),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def block(items: list[dict[str, Any]]) -> dict[str, Any]:
        tokens = [r["tokens"] for r in items if isinstance(r.get("tokens"), (int, float))]
        cites = {k: sum((r.get("citations") or {}).get(k, 0) for r in items)
                 for k in ("total", "tool", "unverified", "supported", "weak", "unsupported")}
        return {
            "n": len(items),
            "score": round(statistics.fmean(r["score"] for r in items), 3) if items else None,
            "correct_rate": round(sum(1 for r in items if r["correct"]) / len(items), 3) if items else None,
            "complete_rate": round(sum(1 for r in items if r["coverage_complete"]) / len(items), 3) if items else None,
            "failed": sum(1 for r in items if r["failed"]),
            "mean_elapsed_seconds": round(statistics.fmean(r["elapsed_seconds"] for r in items), 1) if items else None,
            "mean_tokens": round(statistics.fmean(tokens)) if tokens else None,
            "citations": cites,
        }
    by_mode = {m.value: block([r for r in rows if r["mode"] == m.value]) for m in TaskMode}
    return {"overall": block(rows), "by_mode": {k: v for k, v in by_mode.items() if v["n"]}}


async def run_benchmark(
    rows: list[dict[str, Any]], *, output_dir: Path, runner_factory: RunnerFactory, judge: Any,
    concurrency: int = 1, resume: bool = True,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "results.jsonl"
    done: dict[str, dict[str, Any]] = {}
    if resume and results_path.exists():
        done = {r["id"]: r for r in load_jsonl(results_path)}
    pending = [r for r in rows if r["id"] not in done]
    semaphore = asyncio.Semaphore(max(1, concurrency))
    lock = asyncio.Lock()

    async def one(row: dict[str, Any]) -> None:
        async with semaphore:
            try:
                record = await run_item(row, runner_factory=runner_factory, judge=judge, memory_dir=output_dir / "memory")
            except Exception as exc:  # keep the run going; the row is scored as failed
                record = {"id": row["id"], "mode": row["mode"], "question": row["question"], "answer": "",
                          "correct": False, "score": 0.0, "judge": {"error": f"{type(exc).__name__}: {exc}"},
                          "research_status": "failed", "coverage_complete": False, "failed": True,
                          "elapsed_seconds": 0.0, "tokens": None, "citations": {}}
        async with lock:
            done[row["id"]] = record
            with results_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")

    await asyncio.gather(*(one(row) for row in pending))
    ordered = [done[r["id"]] for r in rows if r["id"] in done]
    summary = {"dataset_items": len(rows), "scored_items": len(ordered), **summarize(ordered)}
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run and score the EvidenceFlow benchmark set.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--output", default="outputs/benchmark/latest", help="Directory for results.jsonl and summary.json.")
    parser.add_argument("--modes", nargs="*", choices=[m.value for m in TaskMode], help="Only these task modes.")
    parser.add_argument("--limit", type=int, help="At most N items per mode.")
    parser.add_argument("--concurrency", type=int, default=1, help="Items run in parallel.")
    parser.add_argument("--max-concurrent", type=int, default=3, help="Research states per item run in parallel.")
    parser.add_argument("--max-replans", type=int, default=2)
    parser.add_argument("--max-adversarial-rounds", type=int, default=0)
    parser.add_argument("--judge-model", help="Defaults to JUDGE_MODEL or the provider's model.")
    parser.add_argument("--no-resume", action="store_true", help="Ignore existing results in --output.")
    return parser


def select_rows(rows: list[dict[str, Any]], modes: list[str] | None, limit: int | None) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    per_mode: dict[str, int] = {}
    for row in rows:
        if modes and row["mode"] not in modes:
            continue
        if limit is not None and per_mode.get(row["mode"], 0) >= limit:
            continue
        per_mode[row["mode"]] = per_mode.get(row["mode"], 0) + 1
        selected.append(row)
    return selected


def main() -> None:
    args = build_parser().parse_args()
    rows = load_jsonl(args.dataset)
    problems = validate_rows(rows)
    if problems:
        raise SystemExit("invalid dataset:\n  " + "\n  ".join(problems))
    rows = select_rows(rows, args.modes, args.limit)
    judge = create_llm(model=args.judge_model or None)
    try:
        summary = asyncio.run(run_benchmark(
            rows, output_dir=Path(args.output), runner_factory=default_runner_factory(args),
            judge=judge, concurrency=args.concurrency, resume=not args.no_resume,
        ))
    finally:
        asyncio.run(judge.close())
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
