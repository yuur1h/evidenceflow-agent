from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from deep_research.evidence_quality import usable_evidence
from deep_research.runner import DeepResearchRunner
from deep_research.schemas import ResearchPlan, ResearchState, WorkerResult, ResearchReport
from deep_research.state_graph import ResearchStateGraph
from deep_research.task_modes import TaskMode


def evidence(**kwargs):
    return {"claim": "Source claim", "url": "https://example.com/source", "evidence_type": "primary", **kwargs}


def runner():
    r = object.__new__(DeepResearchRunner)
    r.task_mode = TaskMode.MULTI_HOP
    r.memory_store = SimpleNamespace(get_conflicts=lambda _: [], get_context_for_query=lambda *a, **k: '', put=lambda e: None)
    r.num_searches = r.num_replans = 0
    r.event_sink = None
    r.state_timeout_seconds = 5
    return r


def assessment(status='resolved', ev=None, tags=None, **result_kwargs):
    r = runner()
    g = ResearchStateGraph()
    tags = tags or ['answer']
    g.add_state(ResearchState('s', 'search', 'x', coverage_tags=tags))
    g.mark_success('s')
    result = WorkerResult(status=status, state_id='s', evidence=ev if ev is not None else [evidence()], **result_kwargs)
    plan = ResearchPlan('x', 'x', coverage_checklist=tags)
    return r, g, plan, result


@pytest.mark.parametrize('item', [{}, {'claim': 'x'}, {'claim': 'x', 'url': 'javascript:alert(1)'}, {'claim': '', 'url': 'https://example.com'}, {'claim': 'x', 'url': 'https://'}, None])
def test_missing_or_invalid_source_rejected(item):
    assert not usable_evidence(item)


@pytest.mark.parametrize('status,ev', [('partial', []), ('resolved', []), ('partial', [evidence()])])
def test_labels_alone_cannot_complete_research(status, ev):
    r, g, plan, result = assessment(status, ev)
    coverage = r._check_coverage(plan, g, [result])
    assert not coverage.complete
    assert 'answer' in coverage.missing


@pytest.mark.parametrize('status,ev,expected', [('partial', [evidence()], 'partial'), ('resolved', [], 'partial'), ('failed', [], 'failed'), ('resolved', [evidence()], 'success')])
@pytest.mark.asyncio
async def test_worker_status_controls_dependencies(status, ev, expected):
    r, g, plan, result = assessment(status, ev)
    g.add_state(ResearchState('child', 'verify', 'verify', dependencies=['s']))
    r.worker = SimpleNamespace(run=AsyncMock(return_value=result))
    await r._run_ready_states('x', g, [g.get_state('s')], [])
    assert g.get_state('s').status.value == expected
    assert bool(g.ready_states()) == (expected == 'success')


@pytest.mark.parametrize('date', ['', 'yesterday', '2020-01-01', '2999-01-01'])
def test_invalid_or_out_of_scope_date_blocks_recency(date):
    r, g, plan, result = assessment(ev=[evidence(date=date)], tags=['recency'])
    plan.scope = {'date_start': '2025-01-01', 'date_end': '2025-12-31'}
    coverage = r._check_coverage(plan, g, [result])
    assert not coverage.complete
    assert coverage.missing_recency


def test_date_in_explicit_scope_passes():
    r, g, plan, result = assessment(ev=[evidence(date='2025-06-01')], tags=['recency'])
    plan.scope = {'date_start': '2025-01-01', 'date_end': '2025-12-31'}
    assert r._check_coverage(plan, g, [result]).complete


def test_missing_time_bounds_do_not_silently_accept_any_date():
    r, g, plan, result = assessment(ev=[evidence(date='2025-06-01')], tags=['recency'])
    assert not r._check_coverage(plan, g, [result]).complete


def test_secondary_source_cannot_cover_official_requirement():
    r, g, plan, result = assessment(ev=[evidence(evidence_type='secondary')], tags=['official'])
    assert not r._check_coverage(plan, g, [result]).complete


def test_conflict_without_candidate_name_still_blocks_completion():
    r, g, plan, result = assessment(candidate_answer='Paris', conflicts=['The founding date is disputed'])
    coverage = r._check_coverage(plan, g, [result])
    assert not coverage.complete
    assert coverage.conflicts
    assert not r._has_sufficient_final_answer(plan, g, [result], coverage)


def test_latest_retry_replaces_old_failure_assessment():
    r, g, plan, result = assessment()
    old = WorkerResult(status='failed', state_id='s', open_questions=['tool timed out'])
    assert r._check_coverage(plan, g, [old, result]).complete


@pytest.mark.parametrize('recover', [True, False])
@pytest.mark.parametrize('initial_status', ['partial', 'failed'])
@pytest.mark.asyncio
async def test_blocked_dependency_retries_with_budget_and_reports_partial(recover, initial_status):
    r = runner()
    r.session_id = 'fixture'
    r.state_history = []
    r.max_concurrent = 1
    r.max_replans = 1
    r.max_adversarial_rounds = 0
    r.llm = SimpleNamespace()
    r.compressor = SimpleNamespace(get_stats=lambda: {})
    plan = ResearchPlan('x', 'x', coverage_checklist=['answer', 'verification'], states=[
        ResearchState('s', 'search', 'search', coverage_tags=['answer']),
        ResearchState('v', 'verify', 'verify', dependencies=['s'], coverage_tags=['verification'])])
    r.planner = SimpleNamespace(create_plan=AsyncMock(return_value=plan), replan=AsyncMock(return_value=[]))
    attempts = []

    async def work(**kwargs):
        sid = kwargs['state'].state_id
        attempts.append(sid)
        success = recover and attempts.count('s') > 1
        return WorkerResult(state_id=sid, status='resolved' if success else initial_status,
                            evidence=[evidence()] if success else [], candidate_answer='Paris')

    r.worker = SimpleNamespace(run=work)
    async def synthesize(**kwargs):
        return ResearchReport('x', '{"answer":"Paris"}', confidence=0.9, coverage=kwargs['coverage'])
    r.synthesizer = SimpleNamespace(synthesize=synthesize)
    output = await r.run('x')
    assert not output.metadata['failed']
    assert output.metadata['num_replans'] == 1
    assert attempts.count('s') == 2
    assert output.metadata['coverage_complete'] == recover
    if recover:
        assert attempts[-1] == 'v'
        assert output.answer == 'Paris'
    else:
        assert 'v' not in attempts
        assert output.metadata['research_status'] == 'partial'
        assert '证据不足' in output.answer
        assert output.report.confidence <= 0.35
