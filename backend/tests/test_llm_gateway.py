import httpx
import pytest

from app.agents.contracts import DiagnosisDraft, NatureTag
from app.agents.diagnostician import llm_hypothesis
from app.gateway.llm import LLMClient


def _client(handler, key="sk-test"):
    return LLMClient(base_url="https://api.example.com/v1", api_key=key,
                     model="deepseek-chat", transport=httpx.MockTransport(handler))


def test_not_available_without_key():
    assert LLMClient(base_url="https://x/v1", api_key="").available is False


async def test_chat_json_parses_response():
    def handler(request):
        return httpx.Response(200, json={
            "model": "deepseek-chat",
            "choices": [{"message": {"content": '{"mechanism": []}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })

    cli = _client(handler)
    assert cli.available is True
    out = await cli.chat_json("sys", "user")
    assert out == {"mechanism": []}
    assert cli.last_call and cli.last_call.ok
    assert cli.last_call.prompt_tokens == 10


async def test_chat_json_strips_code_fence():
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '```json\n{"a": 1}\n```'}}],
        })

    assert await _client(handler).chat_json("s", "u") == {"a": 1}


async def test_401_returns_none_without_retry():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, text="bad key")

    out = await _client(handler).chat_json("s", "u", retries=2)
    assert out is None
    assert calls["n"] == 1          # key 无效不重试


async def test_malformed_json_returns_none():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    assert await _client(handler).chat_json("s", "u") is None


async def test_hypothesis_binds_evidence_and_caps_confidence():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content":
            '{"mechanism":[{"code":"M06","confidence":0.99,"evidence_ids":[7],'
            '"explain":"复杂度可能过高"}],"narrative":"先估算一下数据规模","first_hint_level":2}'
        }}]})

    draft = DiagnosisDraft(nature=[NatureTag(p="P3", code="X")], narrative="待定",
                           confidence=0.0, need_human=True)
    evidence = [{"id": 7, "type": "run_trace", "payload": {"status": "Time Limit Exceeded"}}]
    out = await llm_hypothesis(draft, evidence, "TLE", "int main(){}", _client(handler))

    assert out is not None
    assert out.confidence <= 0.6          # 推断封顶
    assert out.mechanism[0].evidence_ids == [7]
    assert out.need_human is True


async def test_hypothesis_ignores_unknown_evidence_ids():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content":
            '{"mechanism":[{"code":"M06","confidence":0.5,"evidence_ids":[999],"explain":"x"}]}'
        }}]})

    draft = DiagnosisDraft(nature=[NatureTag(p="P3", code="X")], narrative="待定",
                           confidence=0.0, need_human=True)
    evidence = [{"id": 7, "type": "run_trace", "payload": {"status": "TLE"}}]
    out = await llm_hypothesis(draft, evidence, "TLE", "", _client(handler))
    assert out is None  # 不得为模型伪造证据关联。


async def test_hypothesis_without_llm_returns_none():
    draft = DiagnosisDraft(nature=[], narrative="x", confidence=0.0, need_human=True)
    assert await llm_hypothesis(draft, [{"id": 1, "type": "compile_log", "payload": {}}],
                                "WA", "", None) is None


@pytest.mark.parametrize("content,expected", [
    ('{"a":1}', {"a": 1}),
    ('前缀 {"a":2} 后缀', {"a": 2}),
    ("", None),
])
async def test_json_extraction(content, expected):
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    assert await _client(handler).chat_json("s", "u") == expected
