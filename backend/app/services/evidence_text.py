"""证据展示文本的保存边界；不参与原始输入或输出的判题比较。"""

from __future__ import annotations


def prepare_evidence_payload(payload: dict) -> dict:
    """复制日志并转义 PostgreSQL JSONB 不能保存的 NUL，标记保存字段的位置。"""
    escaped_fields: list[str] = []

    def walk(value, path: str):
        if isinstance(value, str):
            if "\0" in value:
                escaped_fields.append(path)
                return value.replace("\0", "\\u0000")
            return value
        if isinstance(value, dict):
            # 证据字段名由服务定义；转义路径遵循 JSON Pointer 的 ~ 和 / 规则。
            return {
                key: walk(child, f"{path}/{str(key).replace('~', '~0').replace('/', '~1')}")
                for key, child in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [walk(child, f"{path}/{index}") for index, child in enumerate(value)]
        return value

    saved = walk(payload, "")
    if escaped_fields:
        saved["escaped_fields"] = sorted(escaped_fields)
    return saved


def has_escaped_input(payload: dict) -> bool:
    """可见转义后的输入只能展示，不能声称可直接复制执行。"""
    fields = payload.get("escaped_fields")
    return isinstance(fields, list) and any(
        isinstance(path, str)
        and (path in ("/input", "/stdin") or path.startswith(("/input/", "/stdin/")))
        for path in fields
    )
