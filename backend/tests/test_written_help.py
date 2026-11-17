"""笔试答疑以本人作答为证据，用模拟模型验证实际交接。"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_written_attempts import attempt, publish, single, submit
from test_written_bank_import import make_records, make_zip, preview
from test_written_progress import publish as publish_candidate
from test_written_review import approval, detail, review, staged

from app.api.v1 import written_help
from app.domain.models import User, WrittenHelpTurn, WrittenQuestionVersion
from app.gateway.llm import LLMClient
from app.infra.db import get_session
from app.main import app


async def wrong_attempt(ctx, question=None):
    if question is None:
        _, _, _, question = await publish(ctx)
    res = await submit(ctx, attempt(question, single("B")))
    assert res.status_code == 200 and not res.json()["correct"]
    return f"/api/v1/written/attempts/{res.json()['id']}/help"


def request_body(message="请讲一下我这道题为什么答错。", after_id=None):
    return {"request_id": str(uuid4()), "after_id": after_id, "message": message}


async def test_both_agents_receive_only_earlier_same_topic_practice(api_context, monkeypatch):
    ctx = api_context
    records = make_records()
    first = records["questions.jsonl"][0]
    records["questions.jsonl"].append({**first, "id": "other-level", "level": "S"})
    root = await staged(ctx, records)
    earlier = await publish_candidate(ctx, root, "q1")
    current = await publish_candidate(ctx, root, "q2")
    other_level = await publish_candidate(ctx, root, "other-level")
    records["questions.jsonl"][0]["stem"] += "另一个题包"
    other_batch = await publish_candidate(ctx, await staged(ctx, records), "q1")
    version = await ctx.session.get(WrittenQuestionVersion, earlier["version_id"])
    version.snapshot_metadata = {k: v for k, v in version.snapshot_metadata.items() if k != "learning_topic"}
    await ctx.session.flush()

    result = await submit(ctx, attempt(earlier, single("B")))
    expected = [{
        "attempt_id": result.json()["id"], "question_id": earlier["id"],
        "revision": earlier["revision"], "question_type": "single_choice", "correct": False,
    }]
    for question, learner in ((earlier, ctx.users.peer), (other_level, None), (other_batch, None)):
        assert (await submit(ctx, attempt(question, single("A")), learner)).status_code == 200
    url = await wrong_attempt(ctx, current)
    assert (await submit(ctx, attempt(earlier, single("A")))).status_code == 200
    packets = []

    def completion(request):
        packet = json.loads(json.loads(request.content)["messages"][1]["content"])
        packets.append(packet)
        response = (
            {"reply": "此前这道同类题答错了。我们从本题的条件开始看。", "references": ["prior_practice"]}
            if "diagnosis" in packet else
            {"focus": "联结本人的前一次同类题练习", "references": ["prior_practice", "attempt"]}
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(response)}}]})

    async def client(*args, **kwargs):
        return LLMClient(base_url="https://mock.test", api_key="mock-only", model="mock",
                         transport=httpx.MockTransport(completion))

    monkeypatch.setattr(written_help, "runtime_client", client)
    body = request_body("我以前做过这类题吗？")
    response = await ctx.client.post(url, headers=ctx.headers(), json=body)
    assert response.status_code == 200, response.text
    assert len(packets) == 2
    assert all(packet["prior_practice"] == expected for packet in packets)
    assert packets[1]["diagnosis"]["references"] == ["prior_practice", "attempt"]
    saved = response.json()
    assert saved["prior_practice"] == expected
    assert saved["tutor"]["source"] == "model"
    replay = await ctx.client.post(url, headers=ctx.headers(), json=body)
    assert replay.json()["prior_practice"] == expected and len(packets) == 2
    history = await ctx.client.get(url, headers=ctx.headers())
    assert history.json()["items"][0]["prior_practice"] == expected


async def test_distinct_model_roles_pass_real_diagnosis_and_replay_without_duplicate_calls(
    api_context, monkeypatch
):
    ctx = api_context
    url = await wrong_attempt(ctx)
    packets = []
    handoff = {
        "hypothesis": "可能误把加法结果记成了 3。",
        "focus": "从一个物体再加一个物体解释为什么总数是 2。",
        "references": ["attempt", "review"],
    }

    def completion(request):
        payload = json.loads(request.content)
        prompt, user_content = [entry["content"] for entry in payload["messages"]]
        packet = json.loads(user_content)
        packets.append((prompt, packet))
        result = (
            handoff
            if "诊断 Agent" in prompt.splitlines()[0]
            else {
                "reply": "先放一个方块，再放一个方块，一共只能数到两个方块。",
                "references": ["review"],
            }
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    async def client(*args, **kwargs):
        return LLMClient(
            base_url="https://mock.test",
            api_key="mock-only",
            model="mock",
            transport=httpx.MockTransport(completion),
        )

    monkeypatch.setattr(written_help, "runtime_client", client)
    body = request_body()
    first = await ctx.client.post(url, headers=ctx.headers(), json=body)
    assert first.status_code == 200, first.text
    answer = first.json()
    assert len(packets) == 2
    assert "诊断 Agent" in packets[0][0].splitlines()[0]
    assert "答疑 Agent" in packets[1][0].splitlines()[0]
    assert packets[1][1]["diagnosis"] == handoff
    assert "diagnosis" not in packets[0][1]
    assert "student_id" not in packets[0][1] and "username" not in packets[0][1]
    assert answer["diagnosis"]["source"] == answer["tutor"]["source"] == "model"
    assert "你选了 B" in answer["comparison"]
    replay = await ctx.client.post(url, headers=ctx.headers(), json=body)
    assert replay.json() == {**answer, "replayed": True}
    conflict = await ctx.client.post(
        url, headers=ctx.headers(), json={**body, "message": "另一问题"}
    )
    assert conflict.status_code == 409 and len(packets) == 2
    followup = await ctx.client.post(
        url, headers=ctx.headers(), json=request_body("还是不懂", after_id=answer["id"])
    )
    assert followup.status_code == 200
    assert packets[-1][1]["history"][0]["reply"] == answer["tutor"]["reply"]


async def test_model_failure_and_invalid_citations_report_separate_fallbacks(
    api_context, monkeypatch
):
    ctx = api_context
    url = await wrong_attempt(ctx)
    calls = []

    def completion(request):
        calls.append(request)
        if len(calls) == 1:
            result = {"hypothesis": None, "focus": "读题核对加法", "references": ["knowledge"]}
            return httpx.Response(
                200, json={"choices": [{"message": {"content": json.dumps(result)}}]}
            )
        return httpx.Response(503)

    async def client(*args, **kwargs):
        return LLMClient(
            base_url="https://mock.test",
            api_key="mock-only",
            model="mock",
            transport=httpx.MockTransport(completion),
        )

    monkeypatch.setattr(written_help, "runtime_client", client)
    res = await ctx.client.post(url, headers=ctx.headers(), json=request_body())
    assert res.status_code == 200
    data = res.json()
    assert data["diagnosis"]["source"] == "reviewed"
    assert data["diagnosis"]["reason"] == "model_output_invalid"
    assert data["diagnosis"]["hypothesis"] is None
    assert data["tutor"]["reason"] == "model_unavailable"
    assert "一加一等于二" in data["tutor"]["reply"]


async def test_only_owner_sees_turns_and_interrupted_request_can_resume(api_context):
    ctx = api_context
    url = await wrong_attempt(ctx)
    for account in (ctx.users.peer, ctx.users.teacher):
        expected = 403 if account.role == "teacher" else 404
        assert (await ctx.client.get(url, headers=ctx.headers(account))).status_code == expected
        assert (
            await ctx.client.post(url, headers=ctx.headers(account), json=request_body())
        ).status_code == expected
    body = request_body()
    abandoned = WrittenHelpTurn(
        attempt_id=int(url.split("/")[-2]),
        request_id=body["request_id"],
        question=body["message"],
        lease_until=datetime.now(UTC) + timedelta(minutes=1),
        claim_token=str(uuid4()),
    )
    ctx.session.add(abandoned)
    await ctx.session.commit()
    busy = await ctx.client.post(url, headers=ctx.headers(), json=body)
    assert busy.status_code == 409 and busy.json()["detail"]["code"] == "written_help_busy"
    listing = await ctx.client.get(url, headers=ctx.headers())
    assert listing.json()["items"] == []
    assert listing.json()["pending"]["request_id"] == body["request_id"]
    abandoned.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    await ctx.session.commit()
    resumed = await ctx.client.post(url, headers=ctx.headers(), json=body)
    assert resumed.status_code == 200 and resumed.json()["id"] == abandoned.id
    assert resumed.json()["tutor"]["reason"] == "model_disabled"
    assert (
        await ctx.client.post(url, headers=ctx.headers(), json=request_body())
    ).status_code == 409
    rows = (await ctx.session.scalars(select(WrittenHelpTurn))).all()
    assert len(rows) == 1 and rows[0].result is not None


async def test_curated_knowledge_fallback_rephrases_and_historical_snapshot_stays_fixed(
    api_context,
):
    ctx = api_context
    records = make_records()
    records["knowledge_points.json"]["nodes"].append(
        {"id": "OI-DEMO-BINARY-TREE", "name": "二叉树节点与边", "is_leaf": True}
    )
    batch = await preview(ctx, make_zip(records))
    root = f"/api/v1/admin/written-bank/batches/{batch['id']}"
    assert (
        await ctx.client.post(root + "/stage", headers=ctx.headers(ctx.users.admin))
    ).status_code == 200
    body = approval((await detail(ctx, root))["review"]["draft"])
    body["draft"].update(
        stem="四层二叉树的节点上限",
        primary_knowledge_point_id="OI-DEMO-BINARY-TREE",
        explanation="四层的节点上限分别是 1、2、4、8，加起来是 15。",
    )
    publication = (await review(ctx, root, body)).json()["review"]["publication"]
    new_question = (
        await ctx.client.get(
            f"/api/v1/written/questions/{publication['id']}", headers=ctx.headers()
        )
    ).json()
    url = await wrong_attempt(ctx, new_question)
    first = (await ctx.client.post(url, headers=ctx.headers(), json=request_body())).json()
    body.update(expected_revision=1, request_id=str(uuid4()))
    body["draft"]["explanation"] = "这是新版解析，不能用于旧版答疑。"
    assert (await review(ctx, root, body)).status_code == 200
    followup = await ctx.client.post(
        url, headers=ctx.headers(), json=request_body("我还是不会", after_id=first["id"])
    )
    assert followup.status_code == 200, followup.text
    data = followup.json()
    assert data["knowledge"]["revision"] == 1
    assert "1+2+4=7" in data["tutor"]["reply"]
    assert "四层的节点上限" in data["tutor"]["reply"]
    assert "新版解析" not in data["tutor"]["reply"]
    history = (await ctx.client.get(url, headers=ctx.headers())).json()
    assert [row["id"] for row in history["items"]] == [first["id"], data["id"]]


async def test_duplicate_arrives_while_model_is_running_without_waiting_for_row_lock(
    api_context, isolated_database, monkeypatch
):
    ctx = api_context
    sessions = async_sessionmaker(isolated_database, expire_on_commit=False)
    async with sessions() as session:
        session.add_all(
            [
                User(id=user.id, username=user.username, role=user.role, password_hash="unused")
                for user in (ctx.users.admin, ctx.users.student)
            ]
        )
        await session.commit()

    async def separate_session():
        async with sessions() as session:
            yield session

    started = asyncio.Event()
    release = asyncio.Event()
    run = written_help.explain
    calls = 0

    async def pause(*args):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return await run(*args)

    monkeypatch.setattr(written_help, "explain", pause)
    monkeypatch.setitem(app.dependency_overrides, get_session, separate_session)
    url = await wrong_attempt(ctx)
    body = request_body()
    headers = ctx.headers()
    first = asyncio.create_task(ctx.client.post(url, json=body, headers=headers))
    try:
        await asyncio.wait_for(started.wait(), 12)
        extra = await asyncio.wait_for(ctx.client.post(url, json=body, headers=headers), 8)
        assert extra.status_code == 409
        assert extra.json()["detail"]["code"] == "written_help_busy"
    finally:
        release.set()
        complete = await asyncio.wait_for(first, 12)
    assert complete.status_code == 200 and calls == 1
    replay = await ctx.client.post(url, headers=headers, json=body)
    assert replay.json()["id"] == complete.json()["id"] and replay.json()["replayed"]
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(WrittenHelpTurn)) == 1
