"""HTTP 文本应能原样保存到 PostgreSQL UTF-8 文本列。"""

from typing import Annotated

from pydantic import AfterValidator


def storage_text(value: str) -> str:
    if "\x00" in value:
        raise ValueError("文本不能包含空字符")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("文本包含无法保存的 Unicode 字符") from exc
    return value


StorageText = Annotated[str, AfterValidator(storage_text)]
