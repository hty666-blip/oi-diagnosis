"""按固定发布版本的审核规则比较客观题答案。"""

import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from app.api.input_types import ObjectId

OptionId = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{1,16}$")]


class OptionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["option", "options"]
    option_ids: list[OptionId] = Field(min_length=1, max_length=26)

    @field_validator("option_ids")
    @classmethod
    def unique_options(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("不能提交重复选项")
        return sorted(values)


class BooleanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["boolean"]
    value: StrictBool


class TextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["text"]
    value: str = Field(strict=True, min_length=1, max_length=4000)

    @field_validator("value")
    @classmethod
    def nonempty_unicode(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请填写答案")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("答案包含无效的 Unicode 字符") from exc
        return value


class AttemptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: ObjectId
    version_id: UUID
    request_id: UUID
    response: Annotated[
        OptionResponse | BooleanResponse | TextResponse, Field(discriminator="kind")
    ]

    def digest(self) -> str:
        content = self.model_dump(mode="json", exclude={"request_id"})
        serialized = json.dumps(content, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("ascii")).hexdigest()


KINDS = {
    "single_choice": "option",
    "multiple_choice": "options",
    "true_false": "boolean",
    "fill_blank": "text",
}


def is_correct(snapshot: dict, response: dict) -> bool:
    kind = KINDS.get(snapshot["question_type"])
    answer = snapshot["answer"]
    rule = snapshot["scoring_rule"]
    if kind is None or rule != ("exact_text" if kind == "text" else "all_or_nothing"):
        raise HTTPException(409, "该题的评分规则不可用，请联系管理员")
    if response["kind"] != kind:
        raise HTTPException(422, "作答格式与题型不符")
    if kind in {"option", "options"}:
        selected = set(response["option_ids"])
        allowed = {option["id"] for option in snapshot["options"]}
        if not selected.issubset(allowed) or (kind == "option" and len(selected) != 1):
            raise HTTPException(422, "请选择本题有效选项，单选题只可选择一个")
        return selected == set(answer["option_ids"])
    if kind == "boolean":
        return response["value"] is answer["value"]
    accepted = answer["accepted"]
    value = response["value"]
    if answer["normalization"] == "trim_outer_whitespace":
        return value.strip() in {text.strip() for text in accepted}
    if answer["normalization"] == "exact":
        return value in accepted
    raise HTTPException(409, "该题的填空比较规则不可用，请联系管理员")
