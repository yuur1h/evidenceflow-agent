from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from deep_research.llm import DashScopeLLM


@pytest.mark.asyncio
@pytest.mark.parametrize('setting,expected', [('', None), ('false', False), ('true', True)])
async def test_thinking_setting_is_sent_in_request(monkeypatch, setting, expected):
    monkeypatch.setenv('QWEN_ENABLE_THINKING', setting)
    llm = DashScopeLLM(api_key='test-key', model='test-model')
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='OK'))],
                               usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=3))
    create = AsyncMock(return_value=response)
    monkeypatch.setattr(llm.client.chat.completions, 'create', create)
    try:
        assert await llm.complete([{'role': 'user', 'content': 'hello'}]) == 'OK'
        request = create.call_args.kwargs
        if expected is None:
            assert 'extra_body' not in request
        else:
            assert request['extra_body']['enable_thinking'] is expected
    finally:
        await llm.close()


@pytest.mark.asyncio
async def test_invalid_thinking_setting_does_not_call_api(monkeypatch):
    monkeypatch.setenv('QWEN_ENABLE_THINKING', 'typo')
    llm = DashScopeLLM(api_key='test-key', model='test-model')
    create = AsyncMock()
    monkeypatch.setattr(llm.client.chat.completions, 'create', create)
    try:
        with pytest.raises(ValueError, match='QWEN_ENABLE_THINKING'):
            await llm.complete([])
        create.assert_not_called()
    finally:
        await llm.close()
