"""Joint evidence assessment: answers backed by agreeing independent sources are delivered
even when coverage has gaps; single-source or contradicted answers are not."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from deep_research.confidence import (
    assess_answer, cross_check_with_llm, extract_fact_check_verdict, same_answer, mentions,
    VERDICT_REFUTED, VERDICT_SUPPORTED, VERDICT_PARTIAL, VERDICT_INSUFFICIENT,
)
from deep_research.schemas import CoverageReport, ResearchPlan, ResearchState, WorkerResult
from deep_research.task_modes import TaskMode
from tests.test_runner_logic import offline_runner, evidence


def ev(url, claim, **kw):
    return {"claim": claim, "url": url, "evidence_type": "secondary", "confidence": "high",
            "provenance": "tool", "support": "supported", **kw}


# ---- text matching -------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("Hampshire", "hampshire"), ("The Beatles", "Beatles"), ("John F. Kennedy", "John F Kennedy"),
    ("Kennedy Space Center", "Kennedy"), ("1,000", "1000"), ("钠", "钠（Sodium）"),
])
def test_same_answer_accepts_equivalents(a, b):
    assert same_answer(a, b)


@pytest.mark.parametrize("a,b", [("Hampshire", "Kent"), ("Sodium", "Potassium"), ("11", "12"), ("", "x")])
def test_same_answer_rejects_different_entities(a, b):
    assert not same_answer(a, b)


def test_mentions_finds_candidate_inside_claim():
    assert mentions("Hampshire", "Jane Austen was born in Steventon, Hampshire, in 1775.")
    assert mentions("John F. Kennedy", "The Kennedy Space Center is named after President John F. Kennedy.")
    assert not mentions("Hampshire", "Jane Austen wrote Pride and Prejudice.")


# ---- multi-hop ----------------------------------------------------------------------

def agreeing_results():
    return [
        WorkerResult(state_id="a", status="resolved", candidate_answer="Hampshire", evidence=[
            ev("https://en.wikipedia.org/wiki/Jane_Austen", "Jane Austen was born in Steventon, Hampshire."),
            ev("https://www.britannica.com/biography/Jane-Austen", "Austen, born in Steventon, Hampshire, England."),
        ]),
        WorkerResult(state_id="v", status="partial", candidate_answer="Hampshire", evidence=[
            ev("https://janeausten.co.uk/", "The Jane Austen's House museum is in Chawton, Hampshire."),
        ], open_questions=[]),
    ]


def test_agreeing_independent_sources_are_deliverable_despite_gaps():
    coverage = CoverageReport(complete=False, covered=["answer"], missing=["verification"], partial_states=["v"])
    a = assess_answer(TaskMode.MULTI_HOP, plan=None, results=agreeing_results(), coverage=coverage,
                      synthesized='{"answer": "Hampshire", "confidence": "medium"}')
    assert a.candidate == "Hampshire"
    assert a.independent_sources == 3
    assert a.level in {"medium", "high"}
    assert a.deliverable
    assert any("独立来源" in r for r in a.reasons)


def test_single_source_with_open_question_is_not_deliverable():
    results = [WorkerResult(state_id="a", status="resolved", candidate_answer="Hampshire",
                            evidence=[ev("https://example.com/x", "Austen was born in Hampshire.")],
                            open_questions=["是否存在同名作者？"])]
    coverage = CoverageReport(complete=False, missing=["verification"], open_questions=["是否存在同名作者？"])
    a = assess_answer(TaskMode.MULTI_HOP, plan=None, results=results, coverage=coverage,
                      synthesized='{"answer": "Hampshire"}')
    assert a.independent_sources == 1
    assert not a.deliverable


def test_competing_candidates_and_conflicts_lower_the_score():
    results = agreeing_results() + [
        WorkerResult(state_id="c", status="resolved", candidate_answer="Kent",
                     evidence=[ev("https://other.org/a", "Some claim that Austen was from Kent.")], conflicts=["Kent vs Hampshire"]),
    ]
    coverage = CoverageReport(complete=False, missing=["verification"], conflicts=["Kent vs Hampshire"])
    a = assess_answer(TaskMode.MULTI_HOP, plan=None, results=results, coverage=coverage, synthesized='{"answer": "Hampshire"}')
    clean = assess_answer(TaskMode.MULTI_HOP, plan=None, results=agreeing_results(),
                          coverage=CoverageReport(complete=False, missing=["verification"]), synthesized='{"answer": "Hampshire"}')
    assert a.score < clean.score
    assert "Kent" in a.competing
    assert not a.deliverable


def test_unverified_evidence_does_not_count():
    results = [WorkerResult(state_id="a", status="resolved", candidate_answer="Sodium", evidence=[
        ev("https://a.com/1", "Sodium has atomic number 11.", provenance="unverified"),
        ev("https://b.com/2", "Sodium has atomic number 11.", support="unsupported"),
    ])]
    a = assess_answer(TaskMode.MULTI_HOP, plan=None, results=results,
                      coverage=CoverageReport(complete=False, missing=["verification"]), synthesized='{"answer": "Sodium"}')
    assert a.independent_sources == 0
    assert not a.deliverable


# ---- fact check ---------------------------------------------------------------------

@pytest.mark.parametrize("text,verdict", [
    ("## 核查结论\n该声明**不成立**：长城无法用肉眼从近地轨道看到。", VERDICT_REFUTED),
    ("## 核查结论\n声明成立，珠穆朗玛峰位于中尼边界。", VERDICT_SUPPORTED),
    ("## 核查结论\n部分支持：年份正确但月份有误。", VERDICT_PARTIAL),
    ("## 核查结论\n证据不足，无法判定。", VERDICT_INSUFFICIENT),
    # the first verdict sentence wins; a later "无法看到 / 不足" must not override it (seen in a live run)
    ("# 事实核查\n\n## 核查结论\n\n**判定：反驳（该声明作为普遍性断言不成立）。**\n\n从低地球轨道用裸眼一般**无法**看到中国长城。"
     "单凭照片证据不足以证明裸眼可见。", VERDICT_REFUTED),
])
def test_fact_check_verdict_extraction(text, verdict):
    assert extract_fact_check_verdict(text) == verdict


def test_fact_check_with_two_sources_is_deliverable():
    results = [
        WorkerResult(state_id="s", status="resolved", candidate_answer="声明不成立", evidence=[
            ev("https://nasa.gov/x", "The Great Wall is not visible to the naked eye from orbit."),
        ]),
        WorkerResult(state_id="c", status="partial", candidate_answer="refuted", evidence=[
            ev("https://scientificamerican.com/y", "Astronauts report the wall cannot be seen unaided."),
        ]),
    ]
    coverage = CoverageReport(complete=False, covered=["supporting_evidence"], missing=["counter_evidence", "verdict"])
    a = assess_answer(TaskMode.FACT_CHECK, plan=None, results=results, coverage=coverage,
                      synthesized="# 事实核查\n\n## 核查结论\n该声明被反驳。\n")
    assert a.candidate == VERDICT_REFUTED
    assert a.deliverable


def test_fact_check_insufficient_verdict_is_never_delivered():
    results = [WorkerResult(state_id="s", status="resolved", evidence=[ev("https://a.com/1", "x"), ev("https://b.com/2", "y")])]
    a = assess_answer(TaskMode.FACT_CHECK, plan=None, results=results,
                      coverage=CoverageReport(complete=False), synthesized="## 核查结论\n证据不足，无法判定。")
    assert not a.deliverable


# ---- research -----------------------------------------------------------------------

def test_research_report_with_most_states_resolved_is_deliverable():
    results = [WorkerResult(state_id=f"s{i}", status="resolved",
                            evidence=[ev(f"https://site{i}.org/p", f"finding {i}")]) for i in range(4)]
    coverage = CoverageReport(complete=False, covered=["scope", "findings", "synthesis"], missing=["limitations"])
    a = assess_answer(TaskMode.RESEARCH, plan=None, results=results, coverage=coverage, synthesized="# report")
    assert a.deliverable and a.level != "low"


def test_research_report_with_little_evidence_is_not_deliverable():
    results = [WorkerResult(state_id="s0", status="partial", evidence=[])]
    a = assess_answer(TaskMode.RESEARCH, plan=None, results=results,
                      coverage=CoverageReport(complete=False, missing=["findings", "synthesis", "limitations"]), synthesized="")
    assert not a.deliverable


# ---- LLM cross-check ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_cross_check_contradiction_blocks_delivery():
    coverage = CoverageReport(complete=False, missing=["verification"])
    a = assess_answer(TaskMode.MULTI_HOP, plan=None, results=agreeing_results(), coverage=coverage, synthesized='{"answer": "Hampshire"}')
    assert a.deliverable
    llm = SimpleNamespace(complete=AsyncMock(return_value='{"verdict":"contradicted","confidence":0.9,"note":"来源说 Kent"}'))
    a = await cross_check_with_llm(llm, query="q", assessment=a, results=agreeing_results(), task_mode=TaskMode.MULTI_HOP)
    assert not a.deliverable
    assert a.cross_check["verdict"] == "contradicted"


@pytest.mark.asyncio
async def test_cross_check_failure_is_advisory():
    coverage = CoverageReport(complete=False, missing=["verification"])
    a = assess_answer(TaskMode.MULTI_HOP, plan=None, results=agreeing_results(), coverage=coverage, synthesized='{"answer": "Hampshire"}')
    llm = SimpleNamespace(complete=AsyncMock(side_effect=RuntimeError("boom")))
    b = await cross_check_with_llm(llm, query="q", assessment=a, results=agreeing_results(), task_mode=TaskMode.MULTI_HOP)
    assert b.deliverable and b.cross_check["verdict"] == "skipped"


# ---- runner integration -------------------------------------------------------------

def gap_plan():
    return ResearchPlan("x", "x", coverage_checklist=["answer", "verification"], states=[
        ResearchState("a", "search", "answer", coverage_tags=["answer", "final_answer"]),
        ResearchState("v", "verify", "verify", coverage_tags=["verification"])])


@pytest.mark.asyncio
async def test_runner_delivers_answer_when_independent_sources_agree():
    async def work(**kwargs):
        sid = kwargs["state"].state_id
        if sid == "a":
            return WorkerResult(state_id=sid, status="resolved", candidate_answer="Paris", evidence=[
                evidence(url="https://en.wikipedia.org/wiki/Paris", claim="Paris is the capital of France."),
                evidence(url="https://britannica.com/place/Paris", claim="Paris, capital of France."),
            ])
        # the verification node fails: coverage stays incomplete
        return WorkerResult(state_id=sid, status="failed", evidence=[], error="timeout")

    r = offline_runner(gap_plan(), work)
    r.max_replans = 0
    out = await r.run("x")
    assert not out.metadata["coverage_complete"]
    assert out.metadata["research_status"] == "answered"
    assert out.answer == "Paris"
    data = json.loads(out.report.content)
    assert data["confidence"] in {"medium", "high"}
    assert "独立来源" in data["uncertainty"]
    assert out.metadata["answer_assessment"]["independent_sources"] == 2


@pytest.mark.asyncio
async def test_runner_still_withholds_single_source_answer():
    async def work(**kwargs):
        sid = kwargs["state"].state_id
        if sid == "a":
            return WorkerResult(state_id=sid, status="resolved", candidate_answer="Paris",
                                evidence=[evidence(claim="Paris is the capital.", evidence_type="secondary", confidence="medium")])
        return WorkerResult(state_id=sid, status="failed", evidence=[], error="timeout")

    r = offline_runner(gap_plan(), work)
    r.max_replans = 0
    out = await r.run("x")
    assert out.metadata["research_status"] == "partial"
    assert out.answer == "证据不足，暂无法确定答案"
    assert json.loads(out.report.content)["candidate_answer"] == "Paris"
