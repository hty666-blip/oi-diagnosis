import json

import httpx
import pytest
from fastapi.exceptions import RequestValidationError

from app.api.errors import request_validation_error_handler
from app.main import app


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://safe-errors"
    ) as result:
        yield result


def assert_safe_details(response, *secrets):
    assert response.status_code == 422, response.text
    details = response.json()["detail"]
    assert details
    assert all(set(error) == {"type", "loc", "msg"} for error in details)
    assert all(isinstance(error["loc"], list) for error in details)
    assert all(isinstance(error["msg"], str) and error["msg"] for error in details)
    assert "input" not in response.json()
    assert "ctx" not in response.json()
    assert "body" not in response.json()
    response.text.encode("utf-8")
    assert not any(0xD800 <= ord(character) <= 0xDFFF for character in response.text)
    for secret in secrets:
        assert secret not in response.text
    return details


async def post_json(client, payload):
    # 保持孤立 surrogate 为 JSON 转义，让非法字符串真正到达服务端校验。
    return await client.post("/api/v1/auth/login", content=json.dumps(payload, ensure_ascii=True),
                             headers={"Content-Type": "application/json"})


async def test_surrogate_in_password_is_safe_422_and_does_not_echo_other_credentials(client):
    response = await post_json(client, {"username": "private-login-name", "password": "private-password-\ud800"})
    details = assert_safe_details(response, "private-password", "private-login-name")
    assert details[0]["loc"] == ["body", "password"]
    assert details[0]["type"] in {"value_error", "string_unicode"}


async def test_surrogate_extra_field_name_is_rendered_safely_without_its_value(client):
    response = await post_json(client, {"username": "同学", "password": "private-password",
                                        "unexpected-\ud800": "private-api-token"})
    details = assert_safe_details(response, "private-password", "private-api-token")
    assert details[0]["type"] in {"extra_forbidden", "string_unicode"}
    assert details[0]["loc"][0] == "body"
    if details[0]["type"] == "extra_forbidden":
        assert "unexpected" in details[0]["loc"][-1]


async def test_nul_in_username_is_rejected_before_storage_and_does_not_echo_credentials(client):
    response = await post_json(client, {"username": "private-login\x00", "password": "private-password"})
    details = assert_safe_details(response, "private-login", "private-password")
    assert details[0]["loc"] == ["body", "username"]
    assert details[0]["type"] == "value_error"


@pytest.mark.parametrize("password", [None, 123, ["private-password"], {"token": "private-api-token"}])
async def test_wrong_password_type_never_echoes_the_supplied_value(client, password):
    response = await post_json(client, {"username": "private-login-name", "password": password})
    details = assert_safe_details(response, "private-login-name", "private-password", "private-api-token")
    assert details[0]["loc"] == ["body", "password"]
    assert details[0]["type"] == "string_type"


async def test_password_length_error_keeps_the_existing_frontend_detail_protocol(client):
    response = await post_json(client, {"username": "同学", "password": "private-password" * 100})
    details = assert_safe_details(response, "private-password")
    assert details[0]["type"] == "string_too_long"
    assert "1024" in details[0]["msg"]


async def test_invalid_json_does_not_echo_original_body_or_decoder_context(client):
    response = await client.post("/api/v1/auth/login", content='{"password":"private-password",',
                                 headers={"Content-Type": "application/json"})
    details = assert_safe_details(response, "private-password")
    assert details[0]["type"] == "json_invalid"


async def test_handler_sanitizes_all_string_locations_and_metadata_without_serializing_context():
    error = RequestValidationError([{
        "type": "bad-type-\ud800", "loc": ("body", "bad-field-\udfff", 2),
        "msg": "不能保存\ud800", "input": "private-password", "body": "private-token",
        "ctx": {"error": ValueError("private-api-key")},
    }], body="private-raw-body")
    response = await request_validation_error_handler(None, error)
    rendered = response.body.decode("utf-8")
    details = json.loads(rendered)["detail"]
    assert set(details[0]) == {"type", "loc", "msg"}
    assert details[0]["loc"][-1] == 2
    assert "private" not in rendered
    assert not any(0xD800 <= ord(character) <= 0xDFFF for character in rendered)
