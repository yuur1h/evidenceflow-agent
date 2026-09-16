"""Offline reproduction checks; does not claim live research accuracy."""
import json
from fastapi.testclient import TestClient
from agent import app
from deep_research.schemas import ResearchState
from deep_research.state_graph import ResearchStateGraph

client = TestClient(app, raise_server_exceptions=False)
results = {}
for path in ['/visualize', '/docs', '/openapi.json']:
    response = client.get(path)
    assert response.status_code == 200, (path, response.text)
    results[path] = response.status_code

graph = ResearchStateGraph()
graph.add_states([
    ResearchState('search', 'search', 'Find evidence', coverage_tags=['fact']),
    ResearchState('verify', 'verify', 'Verify evidence', dependencies=['search']),
])
assert [s.state_id for s in graph.ready_states()] == ['search']
graph.mark_running('search')
assert graph.ready_states() == []
graph.mark_success('search')
assert [s.state_id for s in graph.ready_states()] == ['verify']
assert graph.coverage_status(['fact'])['complete']
graph.mark_success('verify')
assert graph.all_terminal()
results['dependency_scheduling_and_coverage'] = 'passed'

import os
from unittest.mock import patch
from deep_research.llm_config import key_variable
key = key_variable()
with patch.dict(os.environ, {key: ''}):
    response = client.post('/stream', json={'question': 'What is the capital of France?'})
    assert 'event: Error' in response.text
    assert f'{key} is not set' in response.text
    results['unconfigured_stream'] = {'status': response.status_code, 'body': response.text}
print(json.dumps(results, ensure_ascii=False, indent=2))
