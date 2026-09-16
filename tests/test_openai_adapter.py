import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import agent
from deep_research import llm as module
from deep_research.llm_config import model_configured


@pytest.fixture
def sdk(monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'openai')
    monkeypatch.setenv('OPENAI_API_KEY', 'test-openai-key')
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-dashscope-key')
    monkeypatch.setenv('OPENAI_MODEL', 'fixture-openai-model')
    monkeypatch.setenv('QWEN_MODEL', 'fixture-qwen-model')
    monkeypatch.setenv('OPENAI_REASONING_EFFORT', '')
    calls = []
    response = SimpleNamespace(status='completed', output_text='{"answer":"ok"}',
                               usage=SimpleNamespace(input_tokens=11, output_tokens=7, total_tokens=18))
    client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=response)),
                             chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock())), close=AsyncMock())
    def constructor(**kwargs):
        calls.append(kwargs)
        return client
    monkeypatch.setattr(module, 'AsyncOpenAI', constructor)
    return client, calls


@pytest.mark.asyncio
async def test_responses_routing_usage_and_sampling(sdk):
    client, calls = sdk
    llm = module.create_llm()
    text = await llm.complete([{'role': 'user', 'content': 'hello'}], temperature=0.1, max_tokens=1024)
    assert json.loads(text)['answer'] == 'ok'
    assert calls[0]['base_url'] == 'https://api.openai.com/v1'
    assert calls[0]['api_key'] == 'test-openai-key'
    request = client.responses.create.call_args.kwargs
    assert request['model'] == 'fixture-openai-model'
    assert request['input'][0]['content'] == 'hello'
    assert request['max_output_tokens'] == 1024
    assert request['store'] is False
    assert 'temperature' not in request
    assert llm.get_usage()['total_tokens'] == 18
    assert llm.get_usage()['prompt_tokens'] == 11
    client.chat.completions.create.assert_not_called()
    await llm.close()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_schema_and_model_override(sdk, monkeypatch):
    client, _ = sdk
    monkeypatch.setenv('OPENAI_REASONING_EFFORT', 'low')
    await module.create_llm(model='judge-fixture').complete([], response_format={
        'type': 'json_schema', 'json_schema': {'name': 'answer', 'strict': True, 'schema': {'type': 'object'}}})
    request = client.responses.create.call_args.kwargs
    assert request['model'] == 'judge-fixture'
    assert request['text']['format']['strict'] is True
    assert request['text']['format']['type'] == 'json_schema'
    assert request['reasoning'] == {'effort': 'low'}


@pytest.mark.parametrize('status,text', [('incomplete', 'truncated'), ('failed', ''), ('completed', '')])
@pytest.mark.asyncio
async def test_incomplete_or_empty_response_is_not_success(sdk, status, text):
    client, _ = sdk
    client.responses.create.return_value.status = status
    client.responses.create.return_value.output_text = text
    llm = module.create_llm()
    with pytest.raises(RuntimeError):
        await llm.complete([])
    assert llm.get_usage()['total_tokens'] == 18


@pytest.mark.asyncio
async def test_dashscope_backward_compatibility(sdk, monkeypatch):
    client, calls = sdk
    monkeypatch.setenv('LLM_PROVIDER', 'dashscope')
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='qwen result'))],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5))
    llm = module.create_llm()
    assert await llm.complete([], temperature=0.1) == 'qwen result'
    assert calls[0]['api_key'] == 'test-dashscope-key'
    assert 'dashscope.aliyuncs.com' in calls[0]['base_url']
    assert client.chat.completions.create.call_args.kwargs['model'] == 'fixture-qwen-model'
    client.responses.create.assert_not_called()


def test_missing_openai_key_never_uses_dashscope_key(sdk, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY')
    assert not model_configured()
    with pytest.raises(RuntimeError, match='OPENAI_API_KEY'):
        module.create_llm()
    assert sdk[1] == []


def test_invalid_provider_rejected(sdk, monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'typo')
    with pytest.raises(ValueError, match='LLM_PROVIDER'):
        module.create_llm()


def test_status_reports_selected_provider_without_secrets(sdk):
    status = agent._api_key_status()
    assert status['groups'][0]['name'] == 'OpenAI LLM'
    assert status['groups'][0]['model'] == 'fixture-openai-model'
    assert 'test-openai-key' not in json.dumps(status)


def test_missing_key_sse_is_actionable(sdk, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY')
    response = TestClient(agent.app).post('/stream', json={'question': 'x'})
    assert 'event: Error' in response.text
    assert 'OPENAI_API_KEY is not set' in response.text


@pytest.mark.asyncio
async def test_webpage_analysis_uses_selected_provider(sdk):
    from tools.webpage_analyzer import _analyze_with_llm
    client, calls = sdk
    client.responses.create.return_value.output_text = '{"claim":"fixture"}'
    result = json.loads(await _analyze_with_llm('fixture text', 'https://example.com', 'x'))
    assert result['evidence_source'] == 'https://example.com'
    assert calls[0]['api_key'] == 'test-openai-key'
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_judge_uses_openai_adapter(sdk):
    from evaluation.llm_judge import judge_one
    client, _ = sdk
    client.responses.create.return_value.output_text = '{"is_same_object":true,"judgement":"correct"}'
    result = await judge_one(llm=module.create_llm(model='judge-fixture'), question='x', gold_answer='a', predicted_answer='a')
    assert result['is_same_object'] is True
    assert client.responses.create.call_args.kwargs['model'] == 'judge-fixture'


@pytest.mark.asyncio
async def test_real_sdk_serializes_responses_request_without_network(monkeypatch):
    import httpx2
    from openai import AsyncOpenAI
    requests = []

    def respond(request):
        requests.append(request)
        return httpx2.Response(200, json={
            'id': 'resp_fixture', 'object': 'response', 'created_at': 0,
            'status': 'completed', 'model': 'fixture-model',
            'output': [{'id': 'msg_fixture', 'type': 'message', 'role': 'assistant',
                        'status': 'completed', 'content': [{'type': 'output_text', 'text': 'wire-ok', 'annotations': []}]}],
            'usage': {'input_tokens': 5, 'output_tokens': 2, 'total_tokens': 7}})

    transport = httpx2.MockTransport(respond)
    monkeypatch.setenv('OPENAI_REASONING_EFFORT', '')
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: AsyncOpenAI(
        **kw, http_client=httpx2.AsyncClient(transport=transport)))
    llm = module.OpenAILLM(api_key='fixture-key', model='fixture-model')
    try:
        assert await llm.complete([{'role': 'user', 'content': 'ping'}]) == 'wire-ok'
        assert str(requests[0].url) == 'https://api.openai.com/v1/responses'
        assert json.loads(requests[0].content)['store'] is False
        assert llm.get_usage()['total_tokens'] == 7
    finally:
        await llm.close()
