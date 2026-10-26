import json

import pytest

from deep_research.schemas import DeepResearchResult, ResearchReport
from evaluation.llm_judge import load_jsonl
from evaluation.run_benchmark import DEFAULT_DATASET, run_benchmark, select_rows, validate_rows


def test_shipped_dataset_is_valid_and_covers_every_mode():
    rows = load_jsonl(DEFAULT_DATASET)
    assert validate_rows(rows) == []
    assert {r["mode"] for r in rows} == {"multi_hop", "fact_check", "research"}
    assert len(rows) >= 20


@pytest.mark.parametrize('bad,message', [
    ({"id": "x", "mode": "multi_hop", "question": "q"}, "needs answer"),
    ({"id": "x", "mode": "fact_check", "question": "q", "verdict": "maybe"}, "needs verdict"),
    ({"id": "x", "mode": "research", "question": "q", "rubric": []}, "non-empty rubric"),
    ({"id": "x", "mode": "unknown", "question": "q"}, "unknown mode"),
])
def test_validation_catches_malformed_rows(bad, message):
    assert any(message in p for p in validate_rows([bad]))


def test_select_rows_limits_per_mode():
    rows = load_jsonl(DEFAULT_DATASET)
    picked = select_rows(rows, ["multi_hop", "research"], 2)
    assert [r["mode"] for r in picked].count("multi_hop") == 2
    assert [r["mode"] for r in picked].count("research") == 2
    assert not any(r["mode"] == "fact_check" for r in picked)


class StubRunner:
    def __init__(self, row):
        self.row = row

    async def run(self, question):
        mode = self.row["mode"]
        content = {"multi_hop": '{"answer":"Stephen King"}', "fact_check": "# 核查\n结论：声明不成立",
                   "research": "# 报告\n覆盖了部分要点"}[mode]
        answer = "Stephen King" if mode == "multi_hop" else content
        report = ResearchReport(question, content, confidence=0.8)
        meta = {"research_status": "complete", "coverage_complete": True, "failed": False,
                "llm_usage": {"total_tokens": 1200}, "num_searches": 3, "num_replans": 0,
                "citations": {"total": 4, "tool": 3, "unverified": 1, "supported": 3, "weak": 0, "unsupported": 1}}
        return DeepResearchResult(answer=answer, report=report, metadata=meta)


class StubJudge:
    async def complete(self, messages, **kwargs):
        system = messages[0]["content"]
        if "多跳问答评测裁判" in system:
            return '{"is_same_object": true, "judgement": "correct", "reason": "alias"}'
        if "事实核查评测员" in system:
            return '{"verdict": "refuted"}'
        return '{"covered": [true, true, false, true, false]}'


@pytest.mark.asyncio
async def test_stub_run_scores_each_mode_and_resumes(tmp_path):
    rows = select_rows(load_jsonl(DEFAULT_DATASET), None, 1)
    built = []

    def factory(row, memory_dir):
        built.append(row["id"])
        return StubRunner(row)

    summary = await run_benchmark(rows, output_dir=tmp_path, runner_factory=factory, judge=StubJudge())
    assert summary["scored_items"] == 3
    by_mode = summary["by_mode"]
    assert by_mode["multi_hop"]["correct_rate"] == 1.0
    assert by_mode["fact_check"]["correct_rate"] == 1.0  # fc-01 gold is refuted
    assert by_mode["research"]["score"] == 0.6
    assert summary["overall"]["mean_tokens"] == 1200
    assert summary["overall"]["citations"]["unverified"] == 3
    saved = [json.loads(l) for l in (tmp_path / "results.jsonl").read_text().splitlines()]
    assert {r["id"] for r in saved} == {r["id"] for r in rows}

    again = await run_benchmark(rows, output_dir=tmp_path, runner_factory=factory, judge=StubJudge())
    assert len(built) == 3, "resume must not rerun scored items"
    assert again["scored_items"] == 3


@pytest.mark.asyncio
async def test_runner_exception_is_recorded_not_raised(tmp_path):
    class Broken:
        async def run(self, question):
            raise RuntimeError("provider down")

    rows = select_rows(load_jsonl(DEFAULT_DATASET), ["multi_hop"], 1)
    summary = await run_benchmark(rows, output_dir=tmp_path, runner_factory=lambda r, d: Broken(), judge=StubJudge())
    assert summary["overall"]["failed"] == 1
    assert summary["overall"]["correct_rate"] == 0.0
