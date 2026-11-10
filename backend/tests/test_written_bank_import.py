"""使用自编小题验证导入、暂存、权限与事务；不包含采集题正文。"""

import base64
import hashlib
import io
import json
import random
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from sqlalchemy import func, select

from app.domain.models import (
    AdminAudit,
    Problem,
    WrittenCandidate,
    WrittenImportBatch,
    WrittenImportChunk,
)
from app.services import written_bank_package as package

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j4r8AAAAASUVORK5CYII="
)


async def test_large_snapshot_roundtrips_through_small_rows(api_context):
    ctx = api_context
    records = make_records()
    records["knowledge_points.json"]["synthetic_padding"] = base64.b64encode(
        random.Random(42).randbytes(300000)
    ).decode()
    batch = await preview(ctx, make_zip(records))
    sizes = (
        await ctx.session.scalars(
            select(func.length(WrittenImportChunk.content)).where(
                WrittenImportChunk.batch_id == batch["id"]
            )
        )
    ).all()
    assert len(sizes) > 10 and max(sizes) <= 24 * 1024
    result = await ctx.client.get(
        f"/api/v1/admin/written-bank/batches/{batch['id']}/questions",
        headers=ctx.headers(ctx.users.admin),
    )
    assert result.status_code == 200 and result.json()["total"] == 2


async def test_missing_snapshot_chunk_blocks_staging(api_context):
    ctx = api_context
    batch = await preview(ctx)
    row = await ctx.session.scalar(
        select(WrittenImportChunk).where(
            WrittenImportChunk.batch_id == batch["id"], WrittenImportChunk.key == "payload"
        )
    )
    await ctx.session.delete(row)
    await ctx.session.commit()
    result = await ctx.client.post(
        f"/api/v1/admin/written-bank/batches/{batch['id']}/stage",
        headers=ctx.headers(ctx.users.admin),
    )
    assert result.status_code == 409
    assert await ctx.session.scalar(select(func.count()).select_from(WrittenCandidate)) == 0


async def test_corrupt_image_chunk_is_not_served(api_context):
    ctx = api_context
    batch = await preview(ctx)
    row = await ctx.session.scalar(
        select(WrittenImportChunk).where(
            WrittenImportChunk.batch_id == batch["id"], WrittenImportChunk.key == "assets/test.png"
        )
    )
    row.content = "invalid-image"
    await ctx.session.commit()
    result = await ctx.client.get(
        f"/api/v1/admin/written-bank/batches/{batch['id']}/asset?path=assets/test.png",
        headers=ctx.headers(ctx.users.admin),
    )
    assert result.status_code == 409


def make_records():
    first = {
        "id": "q1",
        "paper_id": "paper1",
        "group_id": None,
        "stem": "1+1=?",
        "level": "J",
        "question_type": "single_choice",
        "options": [{"id": "A", "text": "2"}, {"id": "B", "text": "3"}],
        "answer": {"kind": "option", "option_ids": ["A"]},
        "answer_status": "provided_unverified",
        "source_ids": ["src1"],
        "answer_source_id": "src1",
        "issues": [],
        "primary_knowledge_point_id": "KP-08-02-02",
        "secondary_knowledge_point_ids": [],
        "canonical_question_id": "q1",
        "relation_to_canonical": "canonical",
        "review_status": "approved",
        "eligible_for_practice": True,
        "asset_paths": ["assets/test.png"],
    }
    second = deepcopy(first)
    second.update(
        id="q2", stem="2+2=?", relation_to_canonical="numeric_variant", answer_status="conflict"
    )
    return {
        "questions.jsonl": [first, second],
        "question_groups.jsonl": [],
        "papers.jsonl": [{"id": "paper1", "title": "自编测试卷", "source_ids": ["src1"]}],
        "sources.jsonl": [{"id": "src1", "title": "自编测试", "url": "https://example.test"}],
        "knowledge_points.json": {
            "nodes": [
                {"id": "KP-08-02-02", "name": "空间复杂度", "is_leaf": True, "prerequisites": []}
            ]
        },
    }


def make_zip(records=None, *, extra=None, invalid_hash=False):
    records = deepcopy(records or make_records())
    files = {
        name: (
            ("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in value))
            if name.endswith("jsonl")
            else json.dumps(value, ensure_ascii=False)
        ).encode()
        for name, value in records.items()
    }
    files["assets/test.png"] = PNG
    manifest = {
        "files": [
            {"path": name, "bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
            for name, value in files.items()
        ],
        "assets": [],
    }
    if invalid_hash:
        manifest["files"][0]["sha256"] = "0" * 64
    files["manifest.json"] = json.dumps(manifest).encode()
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr("candidate/" + name, value)
        if extra:
            archive.writestr(extra, b"unexpected")
    return buffer.getvalue()


def test_package_preserves_raw_data_and_separates_variants():
    records = make_records()
    payload, summary, assets = package.parse_package(make_zip(records))
    assert payload["questions"] == records["questions.jsonl"]
    assert summary["questions"] == summary["candidate_count"] == 2
    assert summary["source_canonical_count"] == 1
    assert summary["published_count"] == 0
    assert payload["reviews"]["q2"]["independent_id"] == "q2"
    assert payload["reviews"]["q1"]["review_status"] == "needs_review"
    assert "answer_conflict" in payload["reviews"]["q2"]["issues"]
    assert assets["assets/test.png"] == (PNG, "image/png")


@pytest.mark.parametrize("path", ["../outside", "/absolute", "C:/absolute", "candidate/../outside"])
def test_zip_paths_cannot_escape_or_change_roots(path):
    with pytest.raises(package.PackageError):
        package.parse_package(make_zip(extra=path))


def test_backslashes_in_raw_zip_names_are_rejected():
    # Windows 的 ZipInfo 构造器会替换分隔符；修改原始归档名模拟非规范输入。
    raw = make_zip(extra="candidate/outside").replace(b"candidate/outside", b"candidate\\outside")
    with pytest.raises(package.PackageError):
        package.parse_package(raw)


def test_manifest_and_expanded_size_are_enforced(monkeypatch):
    with pytest.raises(package.PackageError, match="摘要"):
        package.parse_package(make_zip(invalid_hash=True))
    monkeypatch.setattr(package, "MAX_EXPANDED", 100)
    with pytest.raises(package.PackageError, match="体积"):
        package.parse_package(make_zip())


@pytest.mark.parametrize("broken", ["answer", "group", "knowledge_cycle", "source"])
def test_invalid_references_and_answers_are_rejected(broken):
    records = make_records()
    q = records["questions.jsonl"][0]
    if broken == "answer":
        q["answer"]["option_ids"] = ["Z"]
    elif broken == "group":
        q["group_id"] = "missing"
    elif broken == "source":
        q["answer_source_id"] = "missing"
    else:
        records["knowledge_points.json"]["nodes"][0]["prerequisites"] = ["KP-08-02-02"]
    with pytest.raises(package.PackageError):
        package.parse_package(make_zip(records))


def test_clear_tag_suggestions_do_not_rewrite_original_labels():
    records = make_records()
    q = records["questions.jsonl"][0]
    q.update(stem="这个算法的空间复杂度是否为常数？", primary_knowledge_point_id=None)
    payload, _, _ = package.parse_package(make_zip(records))
    assert payload["questions"][0]["primary_knowledge_point_id"] is None
    assert payload["reviews"]["q1"]["knowledge_suggestions"][0]["id"] == "KP-08-02-02"


async def preview(ctx, data=None):
    response = await ctx.client.post(
        "/api/v1/admin/written-bank/imports/preview",
        content=data or make_zip(),
        headers={**ctx.headers(ctx.users.admin), "Content-Type": "application/zip"},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_http_stage_is_idempotent_and_never_installs_programming_problems(api_context):
    ctx = api_context
    before = await ctx.session.scalar(select(func.count()).select_from(Problem))
    first = await preview(ctx)
    again = await preview(ctx)
    assert first["id"] == again["id"] and again["replayed"]
    assert await ctx.session.scalar(select(func.count()).select_from(WrittenCandidate)) == 0
    path = f"/api/v1/admin/written-bank/batches/{first['id']}"
    for replayed in [False, True]:
        res = await ctx.client.post(path + "/stage", headers=ctx.headers(ctx.users.admin))
        assert res.status_code == 200 and res.json()["replayed"] is replayed
    assert await ctx.session.scalar(select(func.count()).select_from(WrittenCandidate)) == 2
    candidates = (await ctx.session.scalars(select(WrittenCandidate))).all()
    assert {q.external_id for q in candidates} == {"q1", "q2"}
    assert all(q.review_status == "needs_review" for q in candidates)
    assert await ctx.session.scalar(select(func.count()).select_from(Problem)) == before
    assert (
        await ctx.session.scalar(
            select(func.count()).select_from(AdminAudit).where(AdminAudit.action == "written.stage")
        )
        == 1
    )
    page = await ctx.client.get(path + "/questions?q=q2", headers=ctx.headers(ctx.users.admin))
    assert page.json()["total"] == 1
    detail = await ctx.client.get(path + "/questions/q2", headers=ctx.headers(ctx.users.admin))
    assert detail.json()["record"]["review_status"] == "approved"
    assert detail.json()["normalized"]["review_status"] == "needs_review"


async def test_students_and_teachers_cannot_read_or_write_candidate_bank(api_context):
    ctx = api_context
    batch = await preview(ctx)
    base = "/api/v1/admin/written-bank"
    for user in [ctx.users.student, ctx.users.teacher]:
        for method, path in [
            ("GET", "/batches"),
            ("GET", f"/batches/{batch['id']}/questions"),
            ("GET", f"/batches/{batch['id']}/asset?path=assets/test.png"),
            ("POST", "/imports/preview"),
            ("POST", f"/batches/{batch['id']}/stage"),
        ]:
            result = await ctx.client.request(
                method, base + path, headers=ctx.headers(user), content=b"invalid"
            )
            assert result.status_code == 403


async def test_expired_preview_cannot_stage_and_assets_need_auth(api_context):
    ctx = api_context
    batch = await preview(ctx)
    base = f"/api/v1/admin/written-bank/batches/{batch['id']}"
    image = await ctx.client.get(
        base + "/asset?path=assets/test.png", headers=ctx.headers(ctx.users.admin)
    )
    assert image.content == PNG and image.headers["content-type"] == "image/png"
    assert image.headers["cache-control"] == "no-store"
    assert (await ctx.client.get(base + "/asset?path=assets/test.png")).status_code == 401
    row = await ctx.session.get(WrittenImportBatch, batch["id"])
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await ctx.session.commit()
    result = await ctx.client.post(base + "/stage", headers=ctx.headers(ctx.users.admin))
    assert result.status_code == 410
    assert await ctx.session.scalar(select(func.count()).select_from(WrittenCandidate)) == 0


async def test_staging_rolls_back_all_candidates_and_audit_on_commit_failure(
    api_context, monkeypatch
):
    ctx = api_context
    batch = await preview(ctx)
    commit = ctx.session.commit

    async def fail_after_flush():
        await ctx.session.flush()
        raise RuntimeError("simulated_commit_failure")

    monkeypatch.setattr(ctx.session, "commit", fail_after_flush)
    with pytest.raises(RuntimeError, match="simulated_commit_failure"):
        await ctx.client.post(
            f"/api/v1/admin/written-bank/batches/{batch['id']}/stage",
            headers=ctx.headers(ctx.users.admin),
        )
    await ctx.session.rollback()
    monkeypatch.setattr(ctx.session, "commit", commit)
    assert await ctx.session.scalar(select(func.count()).select_from(WrittenCandidate)) == 0
    saved = await ctx.session.get(WrittenImportBatch, batch["id"])
    assert saved.status == "preview"
    assert (
        await ctx.session.scalar(
            select(func.count()).select_from(AdminAudit).where(AdminAudit.action == "written.stage")
        )
        == 0
    )
