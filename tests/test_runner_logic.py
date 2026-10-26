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


# ---- open questions that belong to another planned state must not block this one ----

def chain_plan():
    return ResearchPlan('x', 'x', coverage_checklist=['answer'], states=[
        ResearchState('s1', 'search', '确认《傲慢与偏见》的作者身份。', coverage_tags=['author']),
        ResearchState('s2', 'search', '检索 Jane Austen 的出生地及其所在的英格兰郡。', dependencies=['s1'], coverage_tags=['answer'])])


@pytest.mark.asyncio
async def test_question_planned_for_another_state_is_deferred_not_blocking():
    attempts = []

    async def work(**kwargs):
        sid = kwargs['state'].state_id
        attempts.append(sid)
        if sid == 's1':
            return WorkerResult(state_id=sid, status='resolved', evidence=[evidence()], candidate_answer='Jane Austen',
                                open_questions=['简·奥斯汀出生于哪个英格兰郡？'])
        return WorkerResult(state_id=sid, status='resolved', evidence=[evidence()], candidate_answer='Hampshire')

    r = offline_runner(chain_plan(), work)
    output = await r.run('x')
    assert attempts == ['s1', 's2']
    assert output.metadata['coverage_complete']


@pytest.mark.asyncio
async def test_question_inside_own_scope_still_blocks():
    attempts = []

    async def work(**kwargs):
        sid = kwargs['state'].state_id
        attempts.append(sid)
        return WorkerResult(state_id=sid, status='resolved', evidence=[evidence()], candidate_answer='Jane Austen',
                            open_questions=['是否存在同名的另一位作者？'])

    r = offline_runner(chain_plan(), work)
    output = await r.run('x')
    assert 's2' not in attempts
    assert output.metadata['research_status'] == 'partial'


# ---- a clean verify state closes the memory conflict it was planned for ----

@pytest.mark.asyncio
async def test_verified_conflict_is_dismissed_in_memory():
    dismissed = []
    plan = ResearchPlan('x', 'x', coverage_checklist=['answer'], states=[
        ResearchState('a', 'search', 'answer', coverage_tags=['answer']),
        ResearchState('vc', 'verify', '核查矛盾事实：A <-> B', coverage_tags=['verification'], backtrack_reason='conflict: A <-> B')])

    async def work(**kwargs):
        return WorkerResult(state_id=kwargs['state'].state_id, status='resolved', evidence=[evidence()], candidate_answer='Paris')

    r = offline_runner(plan, work)
    r.memory_store.dismiss_conflicts_matching = lambda text: dismissed.append(text) or 1
    await r.run('x')
    assert len(dismissed) == 1 and 'A <-> B' in dismissed[0]


# ---- "tag: description" checklist entries must match plain state tags ----

def test_checklist_sentences_are_reduced_to_known_tags():
    planner = DeepResearchPlanner(None)
    plan = planner._parse_plan('x', {
        'states': [{'state_id': 's', 'state_type': 'search', 'description': 'd', 'coverage_tags': ['answer']}],
        'coverage_checklist': ['answer: 给出作者出生地所属的英格兰郡名。', 'verification: 提供可核验的证据', 'official sources']})
    assert plan.coverage_checklist == ['answer', 'verification', 'official sources']
