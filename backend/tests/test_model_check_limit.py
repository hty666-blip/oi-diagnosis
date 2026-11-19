from types import SimpleNamespace

from sqlalchemy import func, select

from app.domain.models import AdminAudit


async def test_teacher_probe_reserves_shared_cooldown_before_model_call(api_context, monkeypatch):
    ctx = api_context
    calls = 0

    async def ping():
        nonlocal calls
        calls += 1
        return True, "离线测试通过"

    async def configured(session):
        return SimpleNamespace(ping=ping, model="offline-model")

    monkeypatch.setattr("app.api.v1.system.runtime_client", configured)
    url = "/api/v1/system/llm/ping"
    first = await ctx.client.post(url, headers=ctx.headers(ctx.users.teacher))
    second = await ctx.client.post(url, headers=ctx.headers(ctx.users.teacher))
    assert first.status_code == 200 and second.status_code == 429
    assert calls == 1
    assert await ctx.session.scalar(select(func.count()).select_from(AdminAudit).where(AdminAudit.action == "ai.connectivity_check")) == 1
    assert (await ctx.client.post(url, headers=ctx.headers(ctx.users.other_teacher))).status_code == 200
    assert calls == 2
    assert (await ctx.client.post(url, headers=ctx.headers())).status_code == 403
