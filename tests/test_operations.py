"""Exercise a private copy of an installation so the working app stays unchanged."""
import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import sys

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
LIMITS = {"request_body_bytes": 128, "render_slots": 1,
          "timeout_seconds": 1.5, "worker_file_bytes": 4096}


@pytest.mark.parametrize(("key", "value"), [
    ("request_body_bytes", 0), ("render_slots", True), ("render_slots", 1.5),
    ("timeout_seconds", -1), ("timeout_seconds", float("inf")),
    ("worker_file_bytes", "4096"),
])
def test_invalid_limit_names_field(tmp_path, key, value):
    from app.operations import load_operations

    path = tmp_path / "operations.json"
    path.write_text(json.dumps({**LIMITS, key: value}))
    with pytest.raises(ValueError, match=key):
        load_operations(path)


def test_duplicate_limit_identifies_file_and_setting(tmp_path):
    from app.operations import load_operations

    path = tmp_path / "operations.json"
    path.write_text('{"render_slots": 2,' + json.dumps(LIMITS)[1:], encoding="utf-8")
    with pytest.raises(ValueError, match=r"operations\.json.*duplicate field render_slots"):
        load_operations(path)


@pytest.fixture
def installation(tmp_path):
    installed = tmp_path / "installed"
    shutil.copytree(ROOT / "app", installed / "app", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "runtime.lock", installed)
    config = installed / "app/config"
    config.mkdir(exist_ok=True)
    (config / "operations.json").write_text(json.dumps(LIMITS))
    return installed


def run_isolated(install, cwd, *args):
    return subprocess.run([sys.executable, "-IB", *args, str(install)], cwd=cwd,
                          capture_output=True, text=True, timeout=15)


@pytest.mark.parametrize("content,reason", [
    (None, "operations.json"), ("{", "operations.json"),
    (json.dumps({k: v for k, v in LIMITS.items() if k != "render_slots"}), "render_slots"),
])
def test_invalid_installation_fails_at_startup(installation, tmp_path, content, reason):
    path = installation / "app/config/operations.json"
    if content is None:
        path.unlink()
    else:
        path.write_text(content)
    result = run_isolated(installation, tmp_path, "-c",
                          "import sys; sys.path.insert(0, sys.argv[1]); import app.server")
    assert result.returncode != 0
    assert reason in result.stderr


def test_configured_limits_reach_requests_and_worker(installation, tmp_path):
    # Exercise the regular -IB jobs.py executable. Only the typesetter is a probe.
    (installation / "app/typeset.py").write_text(
        'import resource\n'
        'def typeset(doc, style, workdir):\n'
        '    limit, _ = resource.getrlimit(resource.RLIMIT_FSIZE)\n'
        '    return f"%PDF-{limit}-{style.body_sp}\\n%%EOF".encode()\n'
    )
    publication = installation / "app/config/publication.json"
    policy = json.loads(publication.read_text())
    policy["defaults"]["body_pt"] = 12.5
    publication.write_text(json.dumps(policy))
    # A different config under the launch directory must have no effect.
    local = tmp_path / "app/config"
    local.mkdir(parents=True)
    (local / "operations.json").write_text("{}")
    result = run_isolated(installation, tmp_path, str(Path(__file__).resolve()))
    assert result.returncode == 0, result.stderr


async def check_live_limits():
    from app.server import app, editor_html

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://local") as api:
        first = await api.get("/")
        assert first.status_code == 200
        assert first.text == editor_html()
        prefix = '<script type="application/json" id="editor-settings">'
        bootstrap = json.loads(first.text.split(prefix, 1)[1].split("</script>", 1)[0])
        assert set(bootstrap) == {
            "defaults", "fonts", "list_presets", "default_list_preset", "default_article_expr", "running_defaults",
            "new_text_styles", "text_defaults", "heading_per_10000", "heading_leading_per_10000",
            "new_paragraph_layout",
        }
        assert bootstrap["default_list_preset"] == "legal"
        assert "list_levels" not in bootstrap["defaults"]
        assert bootstrap["defaults"]["body_pt"] == "12.5"
        stored = await api.post("/api/style/export", json={})
        assert stored.json()["body_sp"] == 819200
        body = b'{"source":"test","style":{}}'.ljust(128)
        response = await api.post("/api/render", content=body)
        assert response.content == b"%PDF-4096-819200\n%%EOF", (response.status_code, response.content)
        for path in ("/api/render", "/api/style/export", "/api/style/open"):
            assert (await api.post(path, content=body + b" ")).status_code == 413

        started = asyncio.Event()
        async def stalled_body():
            started.set()
            await asyncio.Event().wait()
            yield b""

        task = asyncio.create_task(api.post("/api/render", content=stalled_body()))
        try:
            async with asyncio.timeout(3):
                await started.wait()
                assert (await api.post("/api/render", content=body)).status_code == 429
                assert (await task).status_code == 504
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert (await api.post("/api/render", content=body)).status_code == 200


if __name__ == "__main__":
    sys.path.insert(0, sys.argv[1])
    asyncio.run(check_live_limits())
