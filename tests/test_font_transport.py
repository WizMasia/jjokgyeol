"""Bound the optional font frame and serve only the installation's locked assets."""
import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / "runtime.lock").read_text())
FACE = LOCK["fonts"]["nanum-myeongjo"]["regular"]


def frame(request, records, data=b""):
    head = json.dumps({"request": request, "faces": records}).encode()
    return len(head).to_bytes(4, "big") + head + data


@pytest.mark.parametrize("raw", [
    b"", b"\0\0\0\x10{}", frame({}, [], b"extra"),
    frame([], []), frame({}, [{"hash": "../a", "size": 1}], b"x"),
    frame({}, [{"hash": "0" * 64, "size": True}], b"x"),
    frame({}, [{"hash": "0" * 64, "size": 1}], b"x"),
])
def test_invalid_font_frame(raw):
    from app.font_frame import unpack_frame
    with pytest.raises(ValueError):
        unpack_frame(raw)


def test_receive_checks_rights_for_destination_and_rejects_duplicates():
    from app.font_frame import unpack_frame
    data = b"font bytes"
    digest = hashlib.sha256(data).hexdigest()
    record = {"hash": digest, "size": len(data)}
    request = {"source": "원고", "style": {}}
    raw = frame(request, [record], data)
    with patch("app.font_frame.inspect_face", return_value={"hash": digest, "pdf": True, "remote": False}):
        assert unpack_frame(raw, remote=False) == (request, {digest: data})
        with pytest.raises(ValueError):
            unpack_frame(raw, remote=True)
        with pytest.raises(ValueError):
            unpack_frame(frame(request, [record, record], data * 2))


def test_locked_assets_and_binary_render():
    from app.server import app
    from app.style import editor_bootstrap

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as api:
            settings = editor_bootstrap()
            assert settings["font_assets"]["faces"][FACE["sha256"]]["weight"] == "regular"
            for digest in (FACE["sha256"], settings["font_assets"]["notice"]):
                response = await api.get("/api/document-fonts/" + digest)
                assert response.status_code == 200
                assert hashlib.sha256(response.content).hexdigest() == digest
            assert (await api.get("/api/document-fonts/" + "0" * 64)).status_code == 404
            data = Path(FACE["path"]).read_bytes()
            raw = frame({"source": "글꼴 한글", "style": {}, "connection": {"provider": "rule"}},
                        [{"hash": FACE["sha256"], "size": len(data)}], data)
            reply = await api.post("/api/render", content=raw, headers={"Content-Type": "application/x-jjok-font-render"})
            assert reply.status_code == 200, reply.text[:200]
            assert reply.content.startswith(b"%PDF-")
            oversized_json = await api.post("/api/render", content=raw, headers={"Content-Type": "application/json"})
            assert oversized_json.status_code == 413

    asyncio.run(run())
