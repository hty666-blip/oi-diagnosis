"""加密逻辑备份和专用空库恢复验证；不导出模型密钥或环境文件。"""
import asyncio
import copy
import gzip
import hashlib
import io
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import inspect, select, text, tuple_
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.domain.models import Base
from app.infra.db import configure_database_engine
from app.services.problem_versions import version_digest

ROOT = Path(__file__).resolve().parents[3]
EXCLUDED = {"worker_heartbeat", "ai_configuration", "ai_configuration_check", "ai_call_event", "backup_artifact",
            "maintenance_task", "problem_import_draft", "submission_lock"}
MAGIC = b"OI-BACKUP-1\x00"
MAX_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE = 32 * 1024 * 1024
# 显式声明兼容的增量版本；新增表只能补空，不为旧数据伪造历史。
RECEIPT_SOURCE_REVISION = "m7e93da8152b"
RECEIPT_TARGET_REVISION = "n8fa0416c2de"
RECEIPT_TABLE = "hint_message_receipt"
WRITTEN_TARGET_REVISION = "o901b527d3ef"
WRITTEN_TABLES = {"written_import_batch", "written_candidate", "written_import_asset", "written_import_chunk"}
REVIEW_TARGET_REVISION = "p012c638e4fa"
REVIEW_TABLES = {"written_review_revision", "written_question", "written_question_version"}
REVIEW_CANDIDATE_DEFAULTS = {"revision": 0, "draft_metadata": None, "reviewed_by": None, "reviewed_at": None}


class BackupError(Exception):
    pass


def encoded(value):
    def default(item):
        if isinstance(item, datetime):
            return item.isoformat()
        raise TypeError("unsupported_backup_value")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
                      default=default).encode("utf-8")


def tables():
    return [t for t in Base.metadata.sorted_tables if t.name not in EXCLUDED]


def current_revision():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    config = Config()
    config.set_main_option("script_location", str(ROOT / "backend/migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


class BackupIO:
    def __init__(self, *, source_engine=None, directory=None, key_path=None):
        self.source_engine = source_engine
        self.root = Path(directory or settings.backup_directory or ROOT / "data/private/backups").resolve()
        self.key_path = Path(key_path).resolve() if key_path else self.root.parent / "backup.key"

    def key(self, *, create=True):
        path = self.key_path
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(os.urandom(32))
        if not path.is_file():
            raise BackupError("backup_key_unavailable")
        value = path.read_bytes()
        if len(value) != 32:
            raise BackupError("backup_key_unavailable")
        return value

    def path(self, name):
        if not re.fullmatch(r"[a-f0-9-]{36}_[a-f0-9-]{36}\.oibak", name):
            raise BackupError("archive_missing")
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root):
            raise BackupError("archive_missing")
        return path

    async def snapshot(self):
        own = self.source_engine is None
        engine = self.source_engine or create_async_engine(settings.database_url, poolclass=NullPool)
        if own:
            configure_database_engine(engine)
        try:
            async with engine.connect() as conn:
                if conn.dialect.name != "mysql":
                    raise BackupError("mysql_required")
                await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
                await conn.execute(text("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY"))
                revision = await conn.scalar(text("SELECT version_num FROM alembic_version"))
                data = {}
                total = 0
                byte_count = 0
                for table in tables():
                    rows = []
                    keys = list(table.primary_key.columns)
                    last = None
                    while True:
                        query = select(table).order_by(*keys).limit(50)
                        if last is not None:
                            query = query.where(tuple_(*keys) > tuple_(*last))
                        batch = (await conn.execute(query)).mappings().all()
                        for row in batch:
                            item = dict(row)
                            total += 1
                            byte_count += len(encoded(item))
                            if len(rows) >= 100000 or total > 500000 or byte_count > MAX_BYTES:
                                raise BackupError("backup_too_large")
                            rows.append(item)
                        if len(batch) < 50:
                            break
                        last = [batch[-1][column.name] for column in keys]
                    data[table.name] = rows
                await conn.rollback()
            payload = {"format": "oi-backup-v1", "revision": revision, "created_at": datetime.now(UTC).isoformat(), "tables": data}
            raw = encoded(payload)
            if len(raw) > MAX_BYTES:
                raise BackupError("backup_too_large")
            return raw, {name: len(rows) for name, rows in data.items()}, revision
        finally:
            if own:
                await engine.dispose()

    async def create(self, artifact_id, owner):
        raw, counts, revision = await self.snapshot()
        name = f"{artifact_id}_{owner}.oibak"
        path = self.path(name)
        def write():
            nonce = os.urandom(12)
            data = MAGIC + nonce + AESGCM(self.key()).encrypt(nonce, gzip.compress(raw), MAGIC)
            if len(data) > MAX_ARCHIVE:
                raise BackupError("backup_too_large")
            with path.open("xb") as handle:
                handle.write(data)
            return hashlib.sha256(data).hexdigest(), len(data)
        sha, size = await asyncio.to_thread(write)
        return {"file_name": name, "sha256": sha, "size": size, "counts": counts, "revision": revision}

    def read(self, path: Path, expected_hash=None):
        try:
            if path.stat().st_size > MAX_ARCHIVE:
                raise BackupError("backup_too_large")
            data = path.read_bytes()
            if expected_hash and hashlib.sha256(data).hexdigest() != expected_hash:
                raise BackupError("archive_changed")
            if not data.startswith(MAGIC):
                raise BackupError("invalid_archive")
            start = len(MAGIC)
            packed = AESGCM(self.key(create=False)).decrypt(data[start:start + 12], data[start + 12:], MAGIC)
            with gzip.GzipFile(fileobj=io.BytesIO(packed)) as zipped:
                raw = zipped.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise BackupError("backup_too_large")
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get("format") != "oi-backup-v1":
                raise BackupError("schema_mismatch")
            source_revision, target_revision = payload.get("revision"), current_revision()
            compatible = (source_revision == RECEIPT_SOURCE_REVISION
                          and target_revision == RECEIPT_TARGET_REVISION)
            written_compatible = (target_revision == WRITTEN_TARGET_REVISION
                                  and source_revision in {RECEIPT_SOURCE_REVISION, RECEIPT_TARGET_REVISION})
            review_compatible = (target_revision == REVIEW_TARGET_REVISION
                                 and source_revision in {RECEIPT_SOURCE_REVISION, RECEIPT_TARGET_REVISION, WRITTEN_TARGET_REVISION})
            if source_revision != target_revision and not compatible and not written_compatible and not review_compatible:
                raise BackupError("schema_mismatch")
            current_tables = {t.name for t in tables()}
            added_tables = set()
            if compatible or (written_compatible or review_compatible) and source_revision == RECEIPT_SOURCE_REVISION:
                added_tables.add(RECEIPT_TABLE)
            if written_compatible:
                added_tables |= WRITTEN_TABLES
            if review_compatible:
                added_tables |= REVIEW_TABLES
                if source_revision != WRITTEN_TARGET_REVISION:
                    added_tables |= WRITTEN_TABLES
            expected_tables = current_tables - added_tables
            data = payload.get("tables")
            if (not isinstance(data, dict) or set(data) != expected_tables
                    or not added_tables.issubset(current_tables)):
                raise BackupError("invalid_archive")
            if added_tables:
                for table in added_tables:
                    data[table] = []
                payload["revision"] = target_revision
            if review_compatible and source_revision == WRITTEN_TARGET_REVISION:
                for row in data["written_candidate"]:
                    if not isinstance(row, dict) or any(name in row for name in REVIEW_CANDIDATE_DEFAULTS) or row.get("review_status") != "needs_review":
                        raise BackupError("invalid_archive")
                    row.update(REVIEW_CANDIDATE_DEFAULTS)
            # 来源只能由已校验的原始结构版本推导，不能信备份自报的来源字段。
            payload["source_revision"] = source_revision
            return payload
        except InvalidTag as exc:
            raise BackupError("decrypt_failed") from exc
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise BackupError("invalid_archive") from exc

    def verify_url(self, target_url):
        if not target_url:
            raise BackupError("verify_not_configured")
        try:
            target, source = make_url(target_url), make_url(settings.database_url)
        except ArgumentError as exc:
            raise BackupError("verify_target_unsafe") from exc
        if (target.drivername != "mysql+asyncmy" or not target.database
            or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", target.database)
            or "_restore_" not in target.database.lower()
            or target.database.lower() == (source.database or "").lower()
            or (target.host, target.port or 3306) != (source.host, source.port or 3306)):
            raise BackupError("verify_target_unsafe")
        return target

    async def restore(self, payload, target_url, *, keep=False):
        target = self.verify_url(target_url)
        engine = create_async_engine(target, poolclass=NullPool)
        configure_database_engine(engine)
        created = False
        successful = False
        lock_name = "oi-restore-" + hashlib.sha256(target.database.lower().encode()).hexdigest()[:24]
        data = copy.deepcopy(payload["tables"])
        # 恢复后旧登录和失效执行者不复活；已完成判定与审计仍原样保留。
        for row in data.get("app_user", []):
            row["token_version"] += 1
        queued = set()
        for row in data.get("judge_job", []):
            row["owner_token"], row["lease_expires_at"] = None, None
            if row["status"] in ("queued", "running"):
                row["status"], row["available_at"] = "queued", datetime.now(UTC).isoformat()
                queued.add(row["submission_id"])
        for row in data.get("submission", []):
            if row["id"] in queued and row["stage"] != "READY":
                row["stage"] = "QUEUED"
        try:
            async with engine.connect() as conn:
                if await conn.scalar(text("SELECT GET_LOCK(:name, 0)"), {"name": lock_name}) != 1:
                    raise BackupError("verify_busy")
                try:
                    existing = await conn.run_sync(lambda c: inspect(c).get_table_names())
                    if existing:
                        raise BackupError("verify_target_not_empty")
                    created = True
                    # 必须执行迁移，create_all 不会创建教师复核的只追加触发器。
                    env = os.environ.copy()
                    env.update(DATABASE_URL=target.render_as_string(hide_password=False), LLM_ENABLED="false",
                               JUDGE_WORKER_ENABLED="false", MAINTENANCE_WORKER_ENABLED="false")
                    process = await asyncio.create_subprocess_exec(
                        sys.executable, "-m", "alembic", "upgrade", "head", cwd=ROOT / "backend", env=env,
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    try:
                        await asyncio.wait_for(process.wait(), 90)
                        if process.returncode != 0:
                            raise BackupError("restore_migration_failed")
                    finally:
                        if process.returncode is None:
                            process.kill()
                            await process.wait()
                    await conn.commit()
                    async with conn.begin():
                        for table in tables():
                            rows = copy.deepcopy(data[table.name])
                            for row in rows:
                                for col in table.columns:
                                    if getattr(col.type, "impl", None) is not None and col.type.__class__.__name__ == "UTCDateTime" and row.get(col.name):
                                        row[col.name] = datetime.fromisoformat(row[col.name])
                            if rows:
                                await conn.execute(table.insert(), rows)
                        await conn.execute(Base.metadata.tables["ai_configuration"].insert().values(
                            id=1, version=1, enabled=False, base_url="", model="", encrypted_key="", checked_at=None))
                    actual = {}
                    for table in tables():
                        actual[table.name] = [dict(r) for r in (await conn.execute(select(table).order_by(*table.primary_key.columns))).mappings()]
                    if encoded(actual) != encoded(data):
                        raise BackupError("roundtrip_mismatch")
                    for version in actual.get("problem_version", []):
                        if version_digest(version["data"], version["suite"], version["reviewer"]) != version["digest"]:
                            raise BackupError("version_integrity_failed")
                    successful = True
                    return {"counts": {name: len(rows) for name, rows in actual.items()}, "content_verified": True,
                            "credentials_excluded": True, "old_logins_revoked": True, "ai_disabled": True,
                            "source_revision": payload.get("source_revision", payload["revision"]),
                            "restored_revision": current_revision()}
                finally:
                    await conn.rollback()
                    if created and (not keep or not successful):
                        try:
                            await conn.run_sync(Base.metadata.drop_all)
                            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
                            await conn.commit()
                        except Exception as exc:
                            raise BackupError("verify_cleanup_failed") from exc
                    await conn.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name})
        finally:
            await engine.dispose()

    async def verify(self, artifact):
        payload = await asyncio.to_thread(self.read, self.path(artifact.file_name), artifact.sha256)
        return await self.restore(payload, settings.backup_verify_database_url)
