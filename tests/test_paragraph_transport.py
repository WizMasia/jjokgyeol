import asyncio

import httpx
import pytest

from app import jobs, server

URL = "/api/paragraph/align"


@pytest.fixture
def anyio_backend():
    return "asyncio"


pytestmark = pytest.mark.anyio


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(server.app), base_url="http://127.0.0.1") as api:
        yield api


async def test_http_edit_query_and_reset(client):
    source = "작성일"
    marked = "<!-- 쪽결 정렬=오른쪽 -->\n" + source
    for before, line, action, after, alignment in (
        (source, 0, {"align": "right"}, marked, "right"),
        (marked, 1, {}, marked, "right"),
        (marked, 1, {"align": None}, source, None),
    ):
        response = await client.post(URL, json={"source": before, "first_line": line, "last_line": line, **action})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        result = response.json()
        assert isinstance(result.pop("message"), str)
        assert result == {"source": after, "align": alignment, "supported": True}


async def test_http_leading_edit_has_opt_in_response_field(client):
    response = await client.post(URL, json={"source": "첫 줄\n둘째 줄", "first_line": 0,
                                            "last_line": 0, "leading_pt": 18})
    assert response.status_code == 200
    assert response.json()["leading_pt"] == 18
    assert "leading_sp=1179648" in response.json()["source"]

    query = await client.post(URL, json={"source": response.json()["source"], "first_line": 1,
                                         "last_line": 1, "leading": True})
    assert query.json()["leading_pt"] == 18


async def test_http_unsupported_selection(client):
    source = "- 목록"
    response = await client.post(URL, json={"source": source, "first_line": 0, "last_line": 0, "align": "center"})
    assert response.status_code == 200
    result = response.json()
    assert result["supported"] is False and result["message"]
    assert result["source"] == source and result["align"] is None


@pytest.mark.parametrize(("body", "field"), [
    (b"{", "request"),
    (b"\xff", "request"),
    (b'{"source":"ok","first_line":0,"last_line":0,"align":"other"}', "align"),
])
async def test_http_invalid_input_returns_public_error(client, body, field):
    response = await client.post(URL, content=body)
    assert response.status_code == 422
    error = response.json()
    assert error["code"] == "invalid_input" and error["field"] == field and error["message"]


async def test_http_body_and_time_limits(client, monkeypatch):
    monkeypatch.setattr(server, "MAX_BODY", 8)
    assert (await client.post(URL, content=b"123456789")).status_code == 413
    monkeypatch.setattr(jobs, "TIMEOUT", .02)

    async def stalled():
        yield b"{"
        await asyncio.sleep(.2)
        yield b"}"

    assert (await client.post(URL, content=stalled())).status_code == 504


@pytest.mark.parametrize(("token", "status"), [("72.00000000000000001", 422), ("18.0000076293945312501", 200)])
async def test_json_spacing_preserves_decimal_boundary(client, token, status):
    raw = '{"source":"text","first_line":0,"last_line":0,"space_before_pt":' + token + '}'
    response = await client.post(URL, content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == status
    if status == 200:
        assert "space_before_sp=1179649" in response.json()["source"]
