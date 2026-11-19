"""消息响应丢失后，稳定请求编号仅重放已提交回复。"""

import asyncio
import json
from hashlib import sha256
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.security import create_access_token
from app.domain.models import (
    HintMessageReceipt,
    HintSession,
    HintTurn,
    Problem,
    Submission,
    User,
)
from app.infra.db import get_session
from app.main import app
from app.services.tutor_service import TutorService


@pytest.fixture
def compose_calls(monkeypatch):
    calls = []

    async def compose(decision, view, message, client, **kwargs):
        calls.append(message)
        return f"第 {len(calls)} 次解释：检查循环中累计变量的更新，并对照当前执行证据。"

    monkeypatch.setattr("app.services.tutor_service.compose", compose)
    return calls


async def opened(ctx):
    submission = await ctx.submission()
    hs = HintSession(submission_id=submission.id, student_id=ctx.users.student.id)
    ctx.session.add(hs)
    await ctx.session.flush()
    ctx.session.add(HintTurn(session_id=hs.id, role="tutor", level=0, content="请检查当前源码。"))
    await ctx.session.flush()
    return hs, submission


async def post(ctx, session_id, request_id="question-1", content="还是不会", user=None):
    payload = {"content": content}
    if request_id is not None:
        payload["request_id"] = request_id
    return await ctx.client.post(f"/api/v1/sessions/{session_id}/messages",
                                 headers=ctx.headers(user), json=payload)


async def test_repeated_committed_request_reuses_reply_without_appending_or_composing(api_context, compose_calls):
    ctx = api_context
    hs, _ = await opened(ctx)
    first = await post(ctx, hs.id)
    replay = await post(ctx, hs.id)
    assert first.status_code == replay.status_code == 200
    assert first.json()["accepted"] is True and first.json()["replayed"] is False
    assert replay.json()["accepted"] is True and replay.json()["replayed"] is True
    assert replay.json()["reply"] == first.json()["reply"]
    assert compose_calls == ["还是不会"]
    assert await ctx.session.scalar(select(func.count()).select_from(HintTurn)) == 3
    receipt = await ctx.session.scalar(select(HintMessageReceipt))
    assert receipt.actor_id == ctx.users.student.id
    assert receipt.request_hash == sha256(b"question-1").hexdigest()
    assert receipt.content_sha256 == sha256("还是不会".encode()).hexdigest()


async def test_same_content_with_distinct_opaque_ids_is_a_new_question(api_context, compose_calls):
    ctx = api_context
    hs, _ = await opened(ctx)
    # MySQL 默认不区分大小写、尾空格的排序规则不能把这些编号合并。
    identifiers = ["old-draft", "OLD-DRAFT", "old-draft ", "原来的编号"]
    for request_id in identifiers:
        result = await post(ctx, hs.id, request_id)
        assert result.status_code == 200 and result.json()["replayed"] is False
    for request_id in identifiers:
        result = await post(ctx, hs.id, request_id)
        assert result.status_code == 200 and result.json()["replayed"] is True
    assert len(compose_calls) == len(identifiers)
    assert await ctx.session.scalar(select(func.count()).select_from(HintMessageReceipt)) == 4
    assert await ctx.session.scalar(select(func.count()).select_from(HintTurn)) == 9


async def test_legacy_request_without_id_remains_a_new_turn(api_context, compose_calls):
    ctx = api_context
    hs, _ = await opened(ctx)
    for _ in range(2):
        result = await post(ctx, hs.id, None)
        assert result.status_code == 200 and result.json()["replayed"] is False
    assert len(compose_calls) == 2
    assert await ctx.session.scalar(select(func.count()).select_from(HintMessageReceipt)) == 0


async def test_reused_id_with_different_content_is_rejected(api_context, compose_calls):
    ctx = api_context
    hs, _ = await opened(ctx)
    await post(ctx, hs.id)
    result = await post(ctx, hs.id, content="还是不会\n")
    assert result.status_code == 409
    assert len(compose_calls) == 1
    assert await ctx.session.scalar(select(func.count()).select_from(HintTurn)) == 3


@pytest.mark.parametrize("actor,status", [("teacher", 409), ("admin", 409), ("peer", 404),
                                          ("other", 404), ("other_teacher", 404)])
async def test_receipt_cannot_bypass_actor_or_session_permissions(api_context, compose_calls, actor, status):
    ctx = api_context
    hs, _ = await opened(ctx)
    await post(ctx, hs.id)
    result = await post(ctx, hs.id, user=getattr(ctx.users, actor))
    assert result.status_code == status
    assert len(compose_calls) == 1


@pytest.mark.parametrize("request_id", ["", "x" * 101, "bad\x00id", "bad\ud800id"])
async def test_invalid_request_id_is_rejected_before_composing(api_context, compose_calls, request_id):
    ctx = api_context
    hs, _ = await opened(ctx)
    result = await ctx.client.post(f"/api/v1/sessions/{hs.id}/messages",
        headers={**ctx.headers(), "Content-Type": "application/json"},
        content=json.dumps({"content": "还是不会", "request_id": request_id}, ensure_ascii=True))
    assert result.status_code == 422
    assert not compose_calls


@pytest.mark.parametrize("status", ["blocked", "escalated", "resolved"])
async def test_closed_request_is_not_acknowledged_or_saved(api_context, compose_calls, status):
    ctx = api_context
    hs, _ = await opened(ctx)
    hs.status = status  # resolved 无核验提交，读取时应为 unverified。
    await ctx.session.flush()
    result = await post(ctx, hs.id)
    assert result.status_code == 200
    assert result.json()["action"] == "closed"
    assert result.json()["accepted"] is False and result.json()["replayed"] is False
    assert result.json()["status"] == ("unverified" if status == "resolved" else status)
    assert not compose_calls
    assert await ctx.session.scalar(select(func.count()).select_from(HintMessageReceipt)) == 0
    assert await ctx.session.scalar(select(func.count()).select_from(HintTurn)) == 1


async def test_replay_after_verification_keeps_current_binding_status_and_verdict(api_context, compose_calls):
    ctx = api_context
    hs, _ = await opened(ctx)
    first = (await post(ctx, hs.id)).json()
    candidate = await ctx.submission(code_hash="modified-and-verified", verdict="AC",
                                     tests_passed={"scope": "package", "passed": 3, "total": 3})
    verified = await ctx.client.post(f"/api/v1/sessions/{hs.id}/verify", headers=ctx.headers(),
                                     json={"submission_id": candidate.id})
    assert verified.status_code == 200 and verified.json()["status"] == "resolved"
    hs.level = 2
    await ctx.session.flush()
    replay = (await post(ctx, hs.id)).json()
    assert replay["replayed"] is True and replay["accepted"] is True
    assert replay["reply"] == first["reply"]
    assert (replay["status"], replay["level"], replay["verified_submission_id"], replay["verdict"]) == (
        "resolved", 2, candidate.id, "AC")
    history = await ctx.client.get(f"/api/v1/sessions/{hs.id}", headers=ctx.headers())
    assert history.status_code == 200 and history.json()["verdict"] == "AC"
    assert history.json()["verified_submission_id"] == candidate.id
    assert len(history.json()["turns"]) == 4 and len(compose_calls) == 1
    receipt = await ctx.session.scalar(select(HintMessageReceipt))
    assert receipt.response["status"] == "open" and receipt.response["verdict"] == "WA"


@pytest.mark.parametrize("status", ["blocked", "escalated", "unverified"])
async def test_existing_receipt_replays_before_closed_gate_with_current_visible_status(api_context, compose_calls, status):
    ctx = api_context
    hs, _ = await opened(ctx)
    await post(ctx, hs.id)
    hs.status = "resolved" if status == "unverified" else status
    await ctx.session.flush()
    result = await post(ctx, hs.id)
    assert result.status_code == 200
    assert result.json()["accepted"] is True and result.json()["replayed"] is True
    assert result.json()["status"] == status and len(compose_calls) == 1


@pytest.mark.parametrize("actor,status", [("student", 404), ("admin", 409)])
async def test_replay_checks_corrupted_binding_instead_of_returning_cached_success(api_context, compose_calls, actor, status):
    ctx = api_context
    hs, _ = await opened(ctx)
    await post(ctx, hs.id, user=getattr(ctx.users, actor))
    foreign = await ctx.submission(ctx.users.peer)
    hs.verified_submission_id = foreign.id
    await ctx.session.flush()
    result = await post(ctx, hs.id, user=getattr(ctx.users, actor))
    assert result.status_code == status and len(compose_calls) == 1


@pytest.fixture
async def independent_replay_context(api_context, isolated_database):
    sessions = async_sessionmaker(isolated_database, expire_on_commit=False)
    previous = app.dependency_overrides.get(get_session)
    async with sessions() as session:
        student = User(username="replay_student", role="student", class_name="A", password_hash="unused")
        problem = Problem(pid="REPLAY-TEST", title="答疑重放独立测试")
        session.add_all([student, problem])
        await session.flush()
        submission = Submission(problem_id=problem.id, student_id=student.id, code="int main(){}",
                                code_hash="replay-source", stage="READY", verdict="WA",
                                tests_passed={"scope": "package", "passed": 0, "total": 1})
        session.add(submission)
        await session.flush()
        hs = HintSession(submission_id=submission.id, student_id=student.id)
        session.add(hs)
        await session.commit()

    async def independent_session():
        async with sessions() as session:
            yield session

    def headers(user=None):
        user = user or student
        return {"Authorization": "Bearer " + create_access_token(user.id, user.role, user.username)}

    app.dependency_overrides[get_session] = independent_session
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://replay") as client:
            yield SimpleNamespace(client=client, headers=headers, sessions=sessions,
                                  student=student, hs=hs, submission=submission)
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = previous


async def test_concurrent_identical_requests_have_one_pair_and_one_compose(independent_replay_context, compose_calls, monkeypatch):
    import app.services.tutor_service as module

    ctx = independent_replay_context
    original = module.accessible_hint_session
    entered = 0
    together = asyncio.Event()

    async def enter_together(session, user, session_id, *, lock=False):
        nonlocal entered
        entered += 1
        if entered == 2:
            together.set()
        await asyncio.wait_for(together.wait(), timeout=5)
        return await original(session, user, session_id, lock=lock)

    monkeypatch.setattr(module, "accessible_hint_session", enter_together)
    results = await asyncio.gather(post(ctx, ctx.hs.id), post(ctx, ctx.hs.id))
    assert all(result.status_code == 200 for result in results)
    assert sorted(result.json()["replayed"] for result in results) == [False, True]
    assert results[0].json()["reply"] == results[1].json()["reply"]
    assert len(compose_calls) == 1
    async with ctx.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(HintTurn)) == 2
        assert await session.scalar(select(func.count()).select_from(HintMessageReceipt)) == 1


async def test_failed_commit_rolls_back_turns_and_receipt_together(independent_replay_context, compose_calls, monkeypatch):
    ctx = independent_replay_context
    async with ctx.sessions() as session:
        student = await session.get(User, ctx.student.id)

        async def fail_commit():
            await session.flush()  # 已发送INSERT但尚未提交时模拟数据库故障。
            raise RuntimeError("simulated commit failure")

        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="simulated commit failure"):
            await TutorService(session, student).reply(ctx.hs.id, "还是不会", "question-1")
        await session.rollback()
    async with ctx.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(HintTurn)) == 0
        assert await session.scalar(select(func.count()).select_from(HintMessageReceipt)) == 0
    result = await post(ctx, ctx.hs.id)
    assert result.status_code == 200 and result.json()["replayed"] is False
    assert len(compose_calls) == 2  # 外部调用完成但事务未提交时，不声称免除再次调用。
