from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.domain.models import JudgeJob
from app.services.job_service import ERROR_MESSAGES
from app.services.operations_service import GENERIC_FAILURE, safe_failure_message


async def add_job(ctx, status, *, user=None, available_at=None, lease=None, owner="test-owner",
                  error=None, finished_at=None):
    submission = await ctx.submission(
        user=user,
        stage={"queued": "QUEUED", "running": "RUNNING", "failed": "SANDBOX_ERROR",
               "cancelled": "CANCELLED", "succeeded": "READY"}[status],
        verdict="AC" if status == "succeeded" else None,
    )
    fields = {"submission_id": submission.id, "status": status, "attempts": 1,
              "last_error": error, "lease_expires_at": lease, "finished_at": finished_at,
              "owner_token": owner if status == "running" else None}
    if available_at is not None:
        fields["available_at"] = available_at
    row = JudgeJob(**fields)
    ctx.session.add(row)
    await ctx.session.flush()
    return submission, row


async def summary(ctx, user=None):
    return await ctx.client.get("/api/v1/coach/judging", headers=ctx.headers(user or ctx.users.teacher))


async def test_queue_summary_requires_a_teacher_and_authentication(api_context):
    ctx = api_context
    assert (await ctx.client.get("/api/v1/coach/judging")).status_code == 401
    assert (await summary(ctx, ctx.users.student)).status_code == 403


async def test_empty_queue_has_all_statuses_and_no_invented_wait_or_worker_state(api_context):
    response = await summary(api_context)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["counts"] == {"queued": 0, "running": 0, "failed": 0, "cancelled": 0, "succeeded": 0}
    assert result["oldest_queued_wait_seconds"] is None
    assert result["leases"] == {"active": 0, "expired": 0, "missing": 0}
    assert result["recent_failures"] == []
    assert not any("worker" in key or "online" in key for key in result)


async def test_scope_limits_all_counts_leases_waits_and_failures_to_the_visible_class(api_context):
    ctx = api_context
    now = await ctx.session.scalar(select(func.clock_timestamp()))
    await add_job(ctx, "queued", available_at=now - timedelta(seconds=120))
    await add_job(ctx, "running", user=ctx.users.peer, lease=now + timedelta(minutes=3))
    await add_job(ctx, "running", lease=now - timedelta(minutes=1))
    await add_job(ctx, "running", lease=None)
    visible_failure, _ = await add_job(ctx, "failed", error=ERROR_MESSAGES["timeout"], finished_at=now)
    await add_job(ctx, "succeeded")
    await add_job(ctx, "queued", user=ctx.users.other, available_at=now - timedelta(days=1))
    await add_job(ctx, "failed", user=ctx.users.other, error="other-class-private-detail", finished_at=now)
    result = (await summary(ctx)).json()
    assert result["scope"] == {"kind": "class_and_own", "class_name": "A"}
    assert result["counts"] == {"queued": 1, "running": 3, "failed": 1, "cancelled": 0, "succeeded": 1}
    assert 119 <= result["oldest_queued_wait_seconds"] < 130
    assert result["leases"] == {"active": 1, "expired": 1, "missing": 1}
    assert [row["submission_id"] for row in result["recent_failures"]] == [visible_failure.id]
    assert result["recent_failures"][0]["last_error"] == ERROR_MESSAGES["timeout"]
    assert "other-class" not in str(result)
    assert sum((await summary(ctx, ctx.users.admin)).json()["counts"].values()) == 8


async def test_blank_class_teacher_does_not_gain_class_wide_visibility(api_context):
    ctx = api_context
    await add_job(ctx, "queued")
    own, _ = await add_job(ctx, "failed", user=ctx.users.blank_teacher)
    result = (await summary(ctx, ctx.users.blank_teacher)).json()
    assert result["scope"] == {"kind": "own", "class_name": None}
    assert result["counts"]["queued"] == 0
    assert result["counts"]["failed"] == 1
    assert result["recent_failures"][0]["submission_id"] == own.id


async def test_delayed_retry_wait_is_zero_not_a_negative_number(api_context):
    ctx = api_context
    now = await ctx.session.scalar(select(func.clock_timestamp()))
    await add_job(ctx, "queued", available_at=now + timedelta(hours=1))
    result = (await summary(ctx)).json()
    assert result["counts"]["queued"] == 1
    assert result["oldest_queued_wait_seconds"] == 0


async def test_running_lease_buckets_are_mutually_exclusive_and_require_an_owner(api_context):
    ctx = api_context
    now = await ctx.session.scalar(select(func.clock_timestamp()))
    await add_job(ctx, "running", lease=now + timedelta(minutes=3))
    await add_job(ctx, "running", lease=now - timedelta(minutes=3))
    await add_job(ctx, "running", lease=now + timedelta(minutes=3), owner=None)
    await add_job(ctx, "running", lease=now + timedelta(minutes=3), owner=" ")
    await add_job(ctx, "running", lease=None)
    result = (await summary(ctx)).json()
    assert result["counts"]["running"] == 5
    assert result["leases"] == {"active": 1, "expired": 1, "missing": 3}
    assert sum(result["leases"].values()) == result["counts"]["running"]


async def test_cancelled_is_counted_as_a_distinct_saved_fact(api_context):
    ctx = api_context
    await add_job(ctx, "cancelled")
    result = (await summary(ctx)).json()
    assert result["counts"]["cancelled"] == 1
    assert result["counts"]["failed"] == 0
    assert result["recent_failures"] == []


async def test_recent_failures_are_bounded_ordered_and_do_not_return_raw_error_text(api_context):
    ctx = api_context
    now = await ctx.session.scalar(select(func.clock_timestamp()))
    expected = []
    for number in range(7):
        submission, _ = await add_job(
            ctx, "failed", finished_at=now + timedelta(seconds=number),
            error="Authorization: Bearer private-value; password=private-value; internal traceback",
        )
        expected.append(submission.id)
    result = (await summary(ctx)).json()
    assert result["counts"]["failed"] == 7
    assert [row["submission_id"] for row in result["recent_failures"]] == list(reversed(expected[-5:]))
    assert all(row["last_error"] == GENERIC_FAILURE for row in result["recent_failures"])
    assert "private-value" not in str(result)


@pytest.mark.parametrize("message", [None, "", "raw secret", "private traceback"])
def test_unknown_error_text_has_a_fixed_safe_summary(message):
    assert safe_failure_message(message) == GENERIC_FAILURE
