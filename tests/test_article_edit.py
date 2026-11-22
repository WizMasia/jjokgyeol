import asyncio
import os
from pathlib import Path

import httpx
import pytest

from app import jobs, server

URL = "/api/article/pin"


@pytest.fixture
def anyio_backend():
    return "asyncio"


pytestmark = pytest.mark.anyio


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(server.app), base_url="http://127.0.0.1") as api:
        yield api


async def edit(client, source, first, last):
    return await client.post(URL, json={"source": source, "first_line": first, "last_line": last})


@pytest.mark.parametrize(("source", "first", "last", "pinned"), [
    ("\ufeff  ### 제8조의2(대상) **강조**  ###  \r\n본문\r끝\n", 0, 0,
     "\ufeff  ### 제8조의2(대상) **강조** {번호고정}  ###  \r\n본문\r끝\n"),
    ("# 제**8*조*** 목적", 0, 0, "# 제**8*조*** 목적 {번호고정}"),
    ("\n**제8조** 시행\r\n  기간  \r===\n", 1, 3,
     "\n**제8조** 시행\r\n  기간 {번호고정}  \r===\n"),
    ("제8조（목적）\n=", 1, 1, "제8조（목적） {번호고정}\n="),
    ("# 제8조 목적[^주]\n\n[^주]: 각주", 0, 0, "# 제8조 목적[^주] {번호고정}\n\n[^주]: 각주"),
])
async def test_toggle_round_trips_original_source(client, source, first, last, pinned):
    response = await edit(client, source, first, last)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"source": pinned}
    response = await edit(client, pinned, first, last)
    assert response.json() == {"source": source}


@pytest.mark.parametrize(("source", "expected"), [
    ("제8조 목적\r\n{번호고정}\n===\n\n# 제9조 시행", "제8조 목적\r\n===\n\n# 제9조 시행"),
    ("제8조 목적  \r\n{번호고정}\n===", "제8조 목적  \r\n==="),
    ("# **제7조 *목적* {번호고정}** ##", "# **제7조 *목적*** ##"),
    ("# 제7조 목적 **{번호고정}** ###", "# 제7조 목적 ###"),
    ("# 제7조 목적 {**번호**고정}", "# 제7조 목적"),
    ("# 제7조 제목 # {번호고정}", "# 제7조 제목 # #"),
])
async def test_unpin_source_marker_preserves_heading(client, source, expected):
    response = await edit(client, source, 0, 0)
    assert response.status_code == 200
    assert response.json() == {"source": expected}


@pytest.mark.parametrize(("source", "first", "last"), [
    ("- 첫 항\n이어지는 문장\n  # 제8조 인용", 2, 2),
    ("앞 문단\n7. 예시\n제8조 인용\n---", 2, 2),
    ("\x60\x60\x60md\n# 제8조 예시\n\x60\x60\x60\n# 제9조 실제", 1, 1),
    ("제8조 인용 {번호고정}", 0, 0),
    ("# 일반 제목 {번호고정}", 0, 0),
    ("# 제8조 목적\n# 제9조 시행", 0, 1),
    ("# 제8조 목적", True, 0),
    ("# 제8조 목적", -1, 0),
    ("# 제8조 목적", 1, 0),
    ("# 제8조 목적", 0, 1),
    ("# 제8조 목적", 0.0, 0),
    (None, 0, 0),
])
async def test_rejects_ineligible_source_or_selection(client, source, first, last):
    response = await edit(client, source, first, last)
    assert response.status_code == 422
    assert response.json()["field"] == "source"
    assert response.json()["code"] == "invalid_input"


async def test_body_limits_and_input_errors(client, monkeypatch):
    for body in (b"", b"[]", b"{}", b"broken", b"\xff"):
        response = await client.post(URL, content=body)
        assert response.status_code == 422
        assert response.json()["field"] == "source"
    monkeypatch.setattr(server, "MAX_BODY", 8)
    assert (await client.post(URL, content=b"123456789")).status_code == 413
    monkeypatch.setattr(jobs, "TIMEOUT", .015)

    async def stalled():
        yield b"{"
        await asyncio.sleep(.1)
        yield b"}"

    assert (await client.post(URL, content=stalled())).status_code == 504


async def test_markdown_processing_obeys_request_deadline(client, monkeypatch):
    children = Path(f"/proc/self/task/{os.getpid()}/children")
    previous = set(children.read_text().split()) if children.exists() else set()
    monkeypatch.setattr(jobs, "TIMEOUT", .005)
    response = await edit(client, "# 제8조 목적\n" + "- 문장\n" * 16000, 0, 0)
    assert response.status_code == 504
    if children.exists():
        assert set(children.read_text().split()) <= previous


async def test_deadline_during_worker_startup_reaps_child(client, monkeypatch):
    loop = asyncio.get_running_loop()
    connect = loop.connect_write_pipe
    children = Path(f"/proc/self/task/{os.getpid()}/children")
    if not children.exists():
        pytest.skip("Linux child process accounting required")
    previous = set(children.read_text().split())
    starting = asyncio.Event()
    resume = asyncio.Event()

    async def delayed_pipe(*args, **kwargs):
        starting.set()
        await resume.wait()
        return await connect(*args, **kwargs)

    monkeypatch.setattr(loop, "connect_write_pipe", delayed_pipe)
    monkeypatch.setattr(jobs, "TIMEOUT", .05)
    request = asyncio.create_task(edit(client, "# 제8조 목적", 0, 0))
    await asyncio.wait_for(starting.wait(), 2)
    loop.call_later(.1, resume.set)
    try:
        assert (await request).status_code == 504
        assert set(children.read_text().split()) <= previous
    finally:
        resume.set()


async def test_pin_workers_share_the_configured_concurrency_bound(client, monkeypatch):
    limiter = server.PIN_WORKERS
    assert limiter.total_tokens == server.LIMITS.render_slots
    borrowers = [object() for _ in range(limiter.total_tokens)]
    for borrower in borrowers:
        limiter.acquire_on_behalf_of_nowait(borrower)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(jobs, "TIMEOUT", .05)
            assert (await edit(client, "# 제8조 목적", 0, 0)).status_code == 504
    finally:
        for borrower in borrowers:
            limiter.release_on_behalf_of(borrower)
    assert (await edit(client, "# 제8조 목적", 0, 0)).status_code == 200
