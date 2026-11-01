"""HTTP 对象编号范围与数据库 BigInt 对齐，路径和 JSON 保留各自解析规则。"""

from typing import Annotated

from fastapi import Path
from pydantic import Field

MAX_OBJECT_ID = 2**63 - 1

# URL 路径天然是字符串，允许框架先解析整数，再检查存储范围。
ObjectPathId = Annotated[int, Path(gt=0, le=MAX_OBJECT_ID)]
# JSON 正文只接受真正整数，拒绝 bool、浮点数与数字字符串的隐式转换。
ObjectId = Annotated[int, Field(strict=True, gt=0, le=MAX_OBJECT_ID)]
