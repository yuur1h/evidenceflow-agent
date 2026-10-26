from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from deep_research.planner import DeepResearchPlanner
from deep_research.runner import DeepResearchRunner
from deep_research.schemas import ResearchPlan, ResearchReport, ResearchState, WorkerResult
from deep_research.state_graph import ResearchStateGraph
from deep_research.task_modes import TaskMode


def evidence(**kwargs):
    return {"claim": "Source claim", "url": "https://example.com/source", "evidence_type": "primary",
            "confidence": "high", **kwargs}


def offline_runner(plan, work, max_concurrent=2):
    r = object.__new__(DeepResearchRunner)
    r.task_mode = TaskMode.MULTI_HOP
    stored = []
    r.memory_store = SimpleNamespace(get_conflicts=lambda _: [], get_context_for_query=lambda *a, **k: '',
                                     put=stored.append)
    r.stored = stored
    r.num_searches = r.num_replans = 0
    r.event_sink = None
    r.state_timeout_seconds = 5
    r.session_id = 'fixture'
    r.state_history = []
    r.max_concurrent = max_concurrent
    r.max_replans = 1
    r.max_adversarial_rounds = 0
    r.llm = SimpleNamespace()
    r.compressor = SimpleNamespace(get_stats=lambda: {})
    r.planner = SimpleNamespace(create_plan=AsyncMock(return_value=plan), replan=AsyncMock(return_value=[]))
    r.worker = SimpleNamespace(run=work)

    async def synthesize(**kwargs):
        return ResearchReport('x', '{"answer":"Paris"}', confidence=0.9, coverage=kwargs['coverage'])
    r.synthesizer = SimpleNamespace(synthesize=synthesize)
    return r


def answer_plan():
    return ResearchPlan('x', 'x', coverage_checklist=['answer', 'verification'], states=[
        ResearchState('a', 'search', 'answer', coverage_tags=['answer', 'final_answer']),
        ResearchState('v', 'verify', 'verify', coverage_tags=['verification']),
        ResearchState('extra', 'analyze', 'optional detail', dependencies=['a'], coverage_tags=['verification'])])


@pytest.mark.asyncio
async def test_resolved_answer_skips_optional_pending_work():
    attempts = []

    async def work(**kwargs):
        sid = kwargs['state'].state_id
        attempts.append(sid)
        return WorkerResult(state_id=sid, status='resolved', evidence=[evidence()], candidate_answer='Paris')

    output = await offline_runner(answer_plan(), work).run('x')
    assert 'extra' not in attempts
    assert output.metadata['coverage_complete']
    assert output.metadata['research_status'] == 'complete'
    assert output.answer == 'Paris'


@pytest.mark.asyncio
async def test_low_confidence_answer_does_not_stop_early():
    attempts = []

    async def work(**kwargs):
        sid = kwargs['state'].state_id
        attempts.append(sid)
        return WorkerResult(state_id=sid, status='resolved', evidence=[evidence(confidence='medium')],
                            candidate_answer='Paris')

    await offline_runner(answer_plan(), work).run('x')
    assert 'extra' in attempts


@pytest.mark.asyncio
async def test_unsourced_confidence_does_not_count_for_early_stop():
    attempts = []

    async def work(**kwargs):
        sid = kwargs['state'].state_id
        attempts.append(sid)
        return WorkerResult(state_id=sid, status='resolved', candidate_answer='Paris',
                            evidence=[evidence(confidence='medium'), {'claim': 'no url', 'confidence': 'high'}])

    await offline_runner(answer_plan(), work).run('x')
    assert 'extra' in attempts


@pytest.mark.parametrize('mode', [TaskMode.FACT_CHECK, TaskMode.RESEARCH])
@pytest.mark.asyncio
async def test_report_modes_never_stop_early(mode):
    attempts = []

    async def work(**kwargs):
        sid = kwargs['state'].state_id
        attempts.append(sid)
        return WorkerResult(state_id=sid, status='resolved', evidence=[evidence()], candidate_answer='Paris')

    r = offline_runner(answer_plan(), work)
    r.task_mode = mode
    await r.run('x')
    assert 'extra' in attempts


def test_memory_entry_ids_are_stable_across_processes():
    r = offline_runner(answer_plan(), None)
    result = WorkerResult(state_id='s', status='resolved', evidence=[evidence(claim='Paris is the capital')])
    r._collect_to_memory('x', [result])
    # sha1("Paris is the capital")[:16]; hash() would differ under another PYTHONHASHSEED.
    assert r.stored[0].entry_id == 's:0:fa29052a78b51f9a'


def test_partial_states_count_toward_failure_rate_denominator():
    g = ResearchStateGraph()
    g.add_state(ResearchState('f', 'search', 'x'))
    g.add_state(ResearchState('p', 'search', 'x'))
    g.mark_failed('f')
    g.mark_partial('p')
    assert g.failure_rate() == 0.5
    assert not g.should_replan()


@pytest.mark.parametrize('mode', list(TaskMode))
def test_fallback_replan_verifies_every_conflict(mode):
    planner = DeepResearchPlanner(None, task_mode=mode)
    conflicts = ['The founding date is disputed', 'Paris population differs between sources']
    states = planner._fallback_replan_states('x', [], conflicts, [], [{'status': 'resolved', 'candidate_answer': 'Paris'}])
    verify = [s for s in states if s.state_type.value == 'verify']
    assert [s.search_queries[0] for s in verify] == conflicts
