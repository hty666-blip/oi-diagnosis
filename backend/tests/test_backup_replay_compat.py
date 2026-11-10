"""答疑回执迁移只兼容确切旧结构，且保留新备份中的真实回执。"""
import copy
import gzip
import hashlib
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from app.core.config import settings
from app.domain.models import HintMessageReceipt, HintSession, HintTurn, Problem, Submission, User
from app.infra.db import configure_database_engine
from app.services import backup_service
from app.services.backup_service import (
    MAGIC,
    RECEIPT_SOURCE_REVISION,
    RECEIPT_TABLE,
    RECEIPT_TARGET_REVISION,
    WRITTEN_TABLES,
    WRITTEN_TARGET_REVISION,
    BackupError,
    BackupIO,
    encoded,
)


def archive_payload(io, path, payload):
    nonce = os.urandom(12)
    data = MAGIC + nonce + AESGCM(io.key()).encrypt(nonce, gzip.compress(encoded(payload)), MAGIC)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def decoder(monkeypatch, tmp_path):
    names = {"app_user", "hint_session", RECEIPT_TABLE}
    monkeypatch.setattr(backup_service, "tables", lambda: [SimpleNamespace(name=name) for name in names])
    monkeypatch.setattr(backup_service, "current_revision", lambda: RECEIPT_TARGET_REVISION)
    return BackupIO(directory=tmp_path / "backups"), tmp_path / "saved.oibak", names


def test_m7_archive_adds_only_empty_receipts_without_changing_file(decoder):
    io, path, names = decoder
    payload = {"format": "oi-backup-v1", "revision": RECEIPT_SOURCE_REVISION,
               "source_revision": "untrusted-origin", "tables": {name: [] for name in names - {RECEIPT_TABLE}}}
    sha = archive_payload(io, path, payload)
    before = path.read_bytes()
    restored = io.read(path, sha)
    assert restored["revision"] == RECEIPT_TARGET_REVISION
    assert restored["source_revision"] == RECEIPT_SOURCE_REVISION
    assert restored["tables"][RECEIPT_TABLE] == []
    assert set(restored["tables"]) == names
    assert path.read_bytes() == before
    assert json.loads(gzip.decompress(AESGCM(io.key(create=False)).decrypt(
        before[len(MAGIC):len(MAGIC) + 12], before[len(MAGIC) + 12:], MAGIC))) == payload
    with pytest.raises(BackupError, match="archive_changed"):
        io.read(path, "0" * 64)


def test_current_archive_keeps_receipts_and_derives_its_own_source(decoder):
    io, path, names = decoder
    payload = {"format": "oi-backup-v1", "revision": RECEIPT_TARGET_REVISION,
               "source_revision": RECEIPT_SOURCE_REVISION, "tables": {name: [] for name in names}}
    payload["tables"][RECEIPT_TABLE] = [{"id": 1, "response": {"reply": "已保存的讲解"}}]
    sha = archive_payload(io, path, payload)
    restored = io.read(path, sha)
    assert restored["source_revision"] == RECEIPT_TARGET_REVISION
    assert restored["tables"] == payload["tables"]


@pytest.mark.parametrize(("revision", "removed", "added", "error"), [
    (RECEIPT_TARGET_REVISION, RECEIPT_TABLE, None, "invalid_archive"),
    (RECEIPT_TARGET_REVISION, "app_user", None, "invalid_archive"),
    (RECEIPT_TARGET_REVISION, None, "unexpected", "invalid_archive"),
    (RECEIPT_SOURCE_REVISION, None, RECEIPT_TABLE, "invalid_archive"),
    (RECEIPT_SOURCE_REVISION, "app_user", None, "invalid_archive"),
    (RECEIPT_SOURCE_REVISION, None, "unexpected", "invalid_archive"),
    ("l6d82c97041a", None, None, "schema_mismatch"),
    ("future-revision", None, None, "schema_mismatch"),
])
def test_archive_rejects_other_table_or_revision_changes(decoder, revision, removed, added, error):
    io, path, names = decoder
    expected = names - {RECEIPT_TABLE} if revision == RECEIPT_SOURCE_REVISION else names
    data = {name: [] for name in expected}
    if removed:
        data.pop(removed, None)
    if added:
        data[added] = []
    sha = archive_payload(io, path, {"format": "oi-backup-v1", "revision": revision, "tables": data})
    with pytest.raises(BackupError, match=error):
        io.read(path, sha)


def test_m7_exception_does_not_automatically_follow_future_heads(decoder, monkeypatch):
    io, path, names = decoder
    sha = archive_payload(io, path, {"format": "oi-backup-v1", "revision": RECEIPT_SOURCE_REVISION,
                                   "tables": {name: [] for name in names - {RECEIPT_TABLE}}})
    monkeypatch.setattr(backup_service, "current_revision", lambda: "later-head")
    with pytest.raises(BackupError, match="schema_mismatch"):
        io.read(path, sha)


@pytest.mark.parametrize("source", [RECEIPT_SOURCE_REVISION, RECEIPT_TARGET_REVISION])
def test_written_schema_accepts_only_declared_old_tables_and_adds_empty_candidates(decoder, monkeypatch, source):
    io, path, names = decoder
    names.update(WRITTEN_TABLES)
    monkeypatch.setattr(backup_service, "current_revision", lambda: WRITTEN_TARGET_REVISION)
    old_names = names - WRITTEN_TABLES
    if source == RECEIPT_SOURCE_REVISION:
        old_names = old_names - {RECEIPT_TABLE}
    data = {name: [] for name in old_names}
    if RECEIPT_TABLE in data:
        data[RECEIPT_TABLE] = [{"id": 7, "response": {"reply": "已有回执"}}]
    sha = archive_payload(io, path, {"format": "oi-backup-v1", "revision": source, "tables": data})
    restored = io.read(path, sha)
    assert restored["revision"] == WRITTEN_TARGET_REVISION
    assert restored["source_revision"] == source
    assert all(restored["tables"][name] == [] for name in WRITTEN_TABLES)
    if RECEIPT_TABLE in data:
        assert restored["tables"][RECEIPT_TABLE] == data[RECEIPT_TABLE]


def test_current_written_backup_keeps_candidate_data_and_asset_strings(decoder, monkeypatch):
    io, path, names = decoder
    names.update(WRITTEN_TABLES)
    monkeypatch.setattr(backup_service, "current_revision", lambda: WRITTEN_TARGET_REVISION)
    data = {name: [] for name in names}
    data["written_candidate"] = [{"record": {"id": "synthetic"}, "review_status": "needs_review"}]
    data["written_import_chunk"] = [{"key": "assets/test.png", "position": 0, "content": "c3ludGhldGlj"}]
    sha = archive_payload(io, path, {"format": "oi-backup-v1", "revision": WRITTEN_TARGET_REVISION, "tables": data})
    assert io.read(path, sha)["tables"] == data


@pytest.fixture
async def replay_restore_target(monkeypatch):
    url = make_url(os.environ["TEST_DATABASE_URL"])
    name = "test_restore_replay_" + uuid4().hex
    engine = create_async_engine(url)
    monkeypatch.setattr(settings, "database_url", url.render_as_string(hide_password=False))
    async with engine.begin() as conn:
        await conn.execute(CreateSchema(name))
    try:
        yield url.set(database=name).render_as_string(hide_password=False)
    finally:
        async with engine.begin() as conn:
            await conn.execute(DropSchema(name))
        await engine.dispose()


@pytest.fixture
async def replay_backup_source(isolated_database):
    # snapshot 的原生 SQL 读取版本表；用真实源库连接，避免误读父测试库的 Alembic 版本。
    schema = isolated_database.get_execution_options()["schema_translate_map"][None]
    engine = create_async_engine(make_url(os.environ["TEST_DATABASE_URL"]).set(database=schema))
    configure_database_engine(engine)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
            await conn.execute(text("INSERT INTO alembic_version VALUES (:revision)"),
                               {"revision": RECEIPT_TARGET_REVISION})
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.parametrize("legacy", [False, True])
async def test_backup_receipts_and_m7_empty_receipts_restore_completely(
        api_context, replay_backup_source, replay_restore_target, tmp_path, legacy):
    sessions = async_sessionmaker(replay_backup_source, expire_on_commit=False)
    async with sessions() as session:
        student = User(username="replay-backup-student", role="student", password_hash="unused")
        problem = Problem(pid="REPLAY-BACKUP", title="答疑回执恢复")
        session.add_all([student, problem])
        await session.flush()
        submission = Submission(student_id=student.id, problem_id=problem.id,
                                code="int main(){}", code_hash="replay-backup", stage="READY", verdict="WA")
        session.add(submission)
        await session.flush()
        hint = HintSession(submission_id=submission.id, student_id=student.id)
        session.add(hint)
        await session.flush()
        session.add_all([
            HintTurn(session_id=hint.id, role="student", content="这里为什么错？"),
            HintTurn(session_id=hint.id, role="tutor", content="请核对累加的位置。"),
            HintMessageReceipt(session_id=hint.id, request_hash=hashlib.sha256(b"request").hexdigest(),
                               actor_id=student.id, content_sha256=hashlib.sha256("这里为什么错？".encode()).hexdigest(),
                               response={"reply": "请核对累加的位置。", "status": "open"}),
        ])
        await session.commit()
    io = BackupIO(source_engine=replay_backup_source, directory=tmp_path / "private/backups")
    raw, counts, revision = await io.snapshot()
    assert revision == RECEIPT_TARGET_REVISION and counts[RECEIPT_TABLE] == 1
    payload = json.loads(raw)
    # 当前元数据建出的测试源库包含后加的空表；旧版归档应排除这些表。
    for table in WRITTEN_TABLES:
        assert payload["tables"].pop(table) == []
    original_receipts = copy.deepcopy(payload["tables"][RECEIPT_TABLE])
    if legacy:
        payload["revision"] = RECEIPT_SOURCE_REVISION
        del payload["tables"][RECEIPT_TABLE]
    path = tmp_path / "receipt-roundtrip.oibak"
    sha = archive_payload(io, path, payload)
    restored_payload = io.read(path, sha)
    assert restored_payload["tables"][RECEIPT_TABLE] == ([] if legacy else original_receipts)
    restored = await io.restore(restored_payload, replay_restore_target)
    assert restored["source_revision"] == (RECEIPT_SOURCE_REVISION if legacy else revision)
    assert restored["restored_revision"] == WRITTEN_TARGET_REVISION
    assert restored["content_verified"] and restored["counts"][RECEIPT_TABLE] == (0 if legacy else 1)
    assert restored["counts"]["hint_turn"] == 2
    target = create_async_engine(replay_restore_target)
    try:
        async with target.connect() as conn:
            assert await conn.run_sync(lambda c: inspect(c).get_table_names()) == []
    finally:
        await target.dispose()
