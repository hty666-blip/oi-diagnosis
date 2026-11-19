"""PostgreSQL/MySQL 共用的 UTC 时间和数据库墙钟表达式。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import GenericFunction
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """MySQL 存 UTC 无时区值，读取时恢复时区；PG 保留 timestamptz。"""

    impl = DateTime(timezone=True)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(DATETIME(fsp=6) if dialect.name == "mysql" else self.impl)

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        utc = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return utc.replace(tzinfo=None) if dialect.name == "mysql" else utc

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class clock_timestamp(GenericFunction):
    """注册同名 func，测试和业务均使用查询当时的数据库时间。"""

    type = UTCDateTime()
    inherit_cache = True


@compiles(clock_timestamp, "mysql")
def _mysql_clock(element, compiler, **kw):
    # 每条语句重新取时，避免 PostgreSQL now() 的事务起点语义。
    return "UTC_TIMESTAMP(6)"


class after_seconds(GenericFunction):
    type = UTCDateTime()
    inherit_cache = True


@compiles(after_seconds, "postgresql")
def _postgres_after(element, compiler, **kw):
    seconds = compiler.process(list(element.clauses)[0], **kw)
    return f"(clock_timestamp() + ({seconds} * INTERVAL '1 second'))"


@compiles(after_seconds, "mysql")
def _mysql_after(element, compiler, **kw):
    seconds = compiler.process(list(element.clauses)[0], **kw)
    return f"TIMESTAMPADD(MICROSECOND, CAST(({seconds}) * 1000000 AS SIGNED), UTC_TIMESTAMP(6))"


class elapsed_seconds(GenericFunction):
    type = Float()
    inherit_cache = True


@compiles(elapsed_seconds, "postgresql")
def _postgres_elapsed(element, compiler, **kw):
    end, start = (compiler.process(item, **kw) for item in element.clauses)
    return f"EXTRACT(epoch FROM ({end} - {start}))"


@compiles(elapsed_seconds, "mysql")
def _mysql_elapsed(element, compiler, **kw):
    end, start = (compiler.process(item, **kw) for item in element.clauses)
    return f"(TIMESTAMPDIFF(MICROSECOND, {start}, {end}) / 1000000.0)"
