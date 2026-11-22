"""보안 회귀 테스트 / Security regression tests for web app, workers, and parsers."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from threading import BoundedSemaphore

import httpx
import pytest

from app import jobs, server

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(server.app),
        base_url="http://127.0.0.1:8000",
    ) as api:
        yield api


async def test_rejects_disallowed_host_header(client: httpx.AsyncClient) -> None:
    response = await client.get("/", headers={"Host": "evil.example:8000"})
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


async def test_allows_explicitly_configured_host(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JJOKGYEOL_ALLOWED_HOSTS", "editor.internal,10.0.0.5:8080")
    ok = await client.get("/api/reference/settings", headers={"Host": "editor.internal:8000"})
    assert ok.status_code == 200
    ok_port = await client.get("/api/reference/settings", headers={"Host": "10.0.0.5:8080"})
    assert ok_port.status_code == 200
    wrong_port = await client.get("/api/reference/settings", headers={"Host": "10.0.0.5:9999"})
    assert wrong_port.status_code == 403


async def test_security_headers_present_on_html_and_api(client: httpx.AsyncClient) -> None:
    html = await client.get("/")
    assert html.status_code == 200
    assert html.headers["x-content-type-options"] == "nosniff"
    assert html.headers["x-frame-options"] == "SAMEORIGIN"
    assert html.headers["referrer-policy"] == "no-referrer"
    csp = html.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "base-uri 'none'" in csp

    api_res = await client.get("/api/reference/settings")
    assert api_res.status_code == 200
    assert api_res.headers["x-content-type-options"] == "nosniff"
    assert api_res.headers["x-frame-options"] == "SAMEORIGIN"
    assert api_res.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "http://127.0.0.1:9999"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Content-Type": "application/x-www-form-urlencoded"},
        {"Content-Type": "text/plain; charset=utf-8"},
        {"Content-Type": "multipart/form-data; boundary=abc"},
    ],
)
async def test_post_api_blocks_csrf_headers_and_simple_forms(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.post("/api/style/export", content=b"{}", headers=headers)
    assert response.status_code in (403, 415)


async def test_post_api_allows_same_origin_request(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/style/export",
        json={},
        headers={
            "Origin": "http://127.0.0.1:8000",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert response.status_code == 200


async def test_style_file_uses_slot_and_offloads_off_event_loop(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    slots = BoundedSemaphore(1)
    monkeypatch.setattr(server, "STYLE_SLOTS", slots)
    assert slots.acquire(blocking=False)
    try:
        busy = await client.post("/api/style/export", json={})
        assert busy.status_code == 429
    finally:
        slots.release()
    ok = await client.post("/api/style/export", json={})
    assert ok.status_code == 200


async def test_article_pin_acquires_slot_before_reading_body(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    limiter = server.PIN_WORKERS
    borrowers = [object() for _ in range(limiter.total_tokens)]
    for b in borrowers:
        limiter.acquire_on_behalf_of_nowait(b)
    read_started = False

    async def body_stream():
        nonlocal read_started
        read_started = True
        yield b'{"source":"# \xec\xa0\x9c1\xec\xa1\xb0","first_line":0,"last_line":0}'

    try:
        with monkeypatch.context() as patch:
            patch.setattr(jobs, "TIMEOUT", 0.05)
            response = await client.post(
                "/api/article/pin",
                content=body_stream(),
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 504
            assert not read_started
    finally:
        for b in borrowers:
            limiter.release_on_behalf_of(b)
