from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from deep_research.llm import DeepSeekLLM, create_llm
from deep_research.llm_config import display_name, key_variable, model_configured, model_name


@pytest.fixture
def deepseek_env(monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'deepseek')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    monkeypatch.delenv('DEEPSEEK_MODEL', raising=False)
    monkeypatch.delenv('DEEPSEEK_BASE_URL', raising=False)


def test_provider_config_resolves_deepseek(deepseek_env):
    assert key_variable() == 'DEEPSEEK_API_KEY'
    assert model_name() == 'deepseek-chat'
    assert display_name() == 'DeepSeek LLM'
    assert model_configured()


@pytest.mark.asyncio
async def test_create_llm_targets_deepseek_endpoint(deepseek_env):
    llm = create_llm()
    try:
        assert isinstance(llm, DeepSeekLLM)
        assert str(llm.client.base_url).startswith('https://api.deepseek.com/v1')
        assert llm.model == 'deepseek-chat'
    finally:
        await llm.close()


@pytest.mark.asyncio
async def test_missing_key_fails_before_any_request(monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'deepseek')
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    with pytest.raises(RuntimeError, match='DEEPSEEK_API_KEY'):
        create_llm()


@pytest.mark.asyncio
async def test_qwen_thinking_flag_is_not_forwarded(deepseek_env, monkeypatch):
    monkeypatch.setenv('QWEN_ENABLE_THINKING', 'false')
    llm = DeepSeekLLM(api_key='test-key', model='deepseek-chat')
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='OK'))],
                               usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=3))
    create = AsyncMock(return_value=response)
    monkeypatch.setattr(llm.client.chat.completions, 'create', create)
    try:
        assert await llm.complete([{'role': 'user', 'content': 'hello'}], temperature=0.0) == 'OK'
        request = create.call_args.kwargs
        assert 'extra_body' not in request
        assert request['model'] == 'deepseek-chat' and request['temperature'] == 0.0
        assert llm.get_usage()['total_tokens'] == 3
    finally:
        await llm.close()
