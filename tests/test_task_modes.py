import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import agent
from deep_research.planner import DeepResearchPlanner
from deep_research.runner import DeepResearchRunner
from deep_research.schemas import CoverageReport, ResearchPlan, ResearchState, WorkerResult
from deep_research.state_graph import ResearchStateGraph
from deep_research.synthesizer import DeepResearchSynthesizer
from deep_research.task_modes import MODE_COVERAGE, TaskMode


class IdentityCompressor:
    async def compress(self, texts, **kwargs):
        return texts

    def get_stats(self):
        return {}


class FakeEmbedder:
    dim = 4

    def encode(self, text):
        return [1.0, 0.0, 0.0, 0.0]


class NoTools:
    """Returns the fixture source so scripted citations are tool-backed; nothing else."""
    async def search(self, query, **kwargs):
        return {"tool": "search", "query": query,
                "results": [{"title": "fixture", "url": "https://example.com", "snippet": "Fixture claim"}]}


class ScriptedLLM:
    """Fixtures exercise routing, not real model quality."""
    def __init__(self, mode):
        self.mode = mode
        self.prompts = []

    async def complete(self, messages, **kwargs):
        system = messages[0]['content']
        self.prompts.append(system)
        if '引用核验员' in system:
            return '{"verdicts": []}'
        assert f"任务模式：{self.mode.value}" in system
        assert '/Users/marc/' not in system
        if '你负责生成' in system:
            return json.dumps({"states": [{"state_id": "s", "state_type": "search", "description": "fixture research", "coverage_tags": list(MODE_COVERAGE[self.mode])}], "coverage_checklist": list(MODE_COVERAGE[self.mode])})
        if '只完成当前节点' in system:
            return json.dumps({"status": "resolved", "summary": "fixture", "candidate_answer": "Paris", "evidence": [{"claim": "Fixture claim", "source": "fixture", "url": "https://example.com", "evidence_type": "primary", "confidence": "high"}]})
        if '整合研究证据' in system:
            if self.mode == TaskMode.MULTI_HOP:
                return '{"answer":"Paris", "sources":[], "confidence":"high"}'
            return '# 报告\n\n## 结论\n证据不足，保留待核验项。'
        raise AssertionError(system)


@pytest.mark.parametrize('mode', list(TaskMode))
@pytest.mark.asyncio
async def test_full_runner_routes_mode_and_preserves_output(mode, tmp_path):
    llm = ScriptedLLM(mode)
    runner = DeepResearchRunner(llm=llm, task_mode=mode, embedder=FakeEmbedder(), tool_executor=NoTools(),
                                memory_db_path=str(tmp_path / 'memory.db'), max_adversarial_rounds=0, max_replans=0)
    compressor = IdentityCompressor()
    runner.compressor = runner.worker.compressor = runner.synthesizer.compressor = compressor
    result = await runner.run('fixture question')
    assert not result.metadata['failed']
    assert result.metadata['task_mode'] == mode.value
    assert result.answer == ('Paris' if mode == TaskMode.MULTI_HOP else result.report.content)
    assert runner.adversarial.red_agent.task_mode == mode
    assert runner.adversarial.blue_agent.task_mode == mode
    assert any('整合研究证据' in prompt for prompt in llm.prompts)


@pytest.mark.parametrize('mode', list(TaskMode))
def test_api_passes_mode_to_runner(mode, monkeypatch):
    monkeypatch.setattr(agent, 'DeepResearchRunner', lambda **kwargs: SimpleNamespace(**kwargs))
    request = agent.QueryRequest(question='x', task_mode=mode.value)
    assert agent._build_runner(request).task_mode == mode


def test_default_is_backward_compatible():
    assert agent.QueryRequest(question='x').task_mode == TaskMode.MULTI_HOP


@pytest.mark.parametrize('path', ['/', '/stream', '/ag-ui'])
def test_invalid_mode_rejected_before_model_call(path):
    response = TestClient(agent.app).post(path, json={'question': 'x', 'task_mode': 'invalid'})
    assert response.status_code == 422


@pytest.mark.parametrize('mode', [TaskMode.FACT_CHECK, TaskMode.RESEARCH])
def test_non_qa_fallback_covers_required_dimensions(mode):
    planner = DeepResearchPlanner(None, task_mode=mode)
    for plan in [planner._fallback_plan('当前情况'), planner._parse_plan('当前情况', {'states': []})]:
        assert set(MODE_COVERAGE[mode]) <= set(plan.coverage_checklist)
        assert 'recency' in plan.coverage_checklist
        tags = {tag for state in plan.states for tag in state.coverage_tags}
        assert set(plan.coverage_checklist) <= tags
        assert not any('final answer candidate' in q for state in plan.states for q in state.search_queries)


@pytest.mark.parametrize('mode', [TaskMode.FACT_CHECK, TaskMode.RESEARCH])
def test_entity_candidate_cannot_early_stop_report(mode):
    runner = object.__new__(DeepResearchRunner)
    runner.task_mode = mode
    graph = ResearchStateGraph()
    graph.add_state(ResearchState('s', 'search', 'x', coverage_tags=['final_answer']))
    candidate = WorkerResult(state_id='s', status='resolved', candidate_answer='Paris', evidence=[{'confidence': 'high'}])
    assert not runner._has_sufficient_final_answer(ResearchPlan('x', 'x'), graph, [candidate], CoverageReport(complete=False))


@pytest.mark.parametrize('mode', [TaskMode.FACT_CHECK, TaskMode.RESEARCH])
@pytest.mark.asyncio
async def test_failed_synthesis_does_not_invent_entity_answer(mode):
    class FailingLLM:
        async def complete(self, *args, **kwargs):
            raise RuntimeError('offline fixture')

    synthesizer = DeepResearchSynthesizer(FailingLLM(), task_mode=mode, compressor=IdentityCompressor())
    report = await synthesizer.synthesize(query='x', plan=ResearchPlan('x', 'x'), results=[],
                                         state_history=[], states=[], coverage={'missing': ['sources']})
    assert report.content.startswith('# ')
    assert '证据不足' in report.content
    assert 'sources' in report.content
