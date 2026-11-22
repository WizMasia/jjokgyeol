"""Drive each decision API through the render endpoint and read the printed line ends."""
from email import policy
from email.parser import BytesParser
import json

from fastapi.testclient import TestClient
from playwright.sync_api import expect
import pytest

from app.server import app
from app.style import from_editor_style
from settings_ui import open_settings
from test_editor import browser
from test_slice import server_url
from test_decision import SOURCE, STYLE, model_server, scan


def choose_layout(path, data):
    if path.endswith("/responses"):
        prompt = data["input"]
        if isinstance(prompt, list):
            prompt = prompt[-1]["content"]
        if isinstance(prompt, list):
            prompt = prompt[0]["text"]
    elif path.endswith(":generateContent"):
        prompt = data["contents"][-1]["parts"][0]["text"]
    else:
        prompt = data["messages"][-1]["content"]
        if isinstance(prompt, list):
            prompt = prompt[0]["text"]

    options = json.loads(prompt)
    picked = min(options, key=lambda key: options[key][0]["end_glyph"])
    text = json.dumps({"id": picked})
    if path.endswith("/responses"):
        return {"status": "completed", "output": [{"type": "message", "role": "assistant",
                "status": "completed", "content": [{"type": "output_text", "text": text}]}]}
    if path.endswith(":generateContent"):
        return {"candidates": [{"finishReason": "STOP", "content": {"role": "model",
                "parts": [{"text": text}]}}]}
    if path.endswith("/messages"):
        return {"type": "message", "role": "assistant", "stop_reason": "end_turn",
                "content": [{"type": "text", "text": text}]}
    message = {"role": "assistant", "content": text}
    if path.endswith("/api/chat"):
        return {"done": True, "done_reason": "stop", "message": message}
    return {"choices": [{"index": 0, "message": message, "finish_reason": "stop"}]}


@pytest.mark.parametrize("provider,suffix", [
    ("responses", "/responses"), ("chat", "/chat/completions"),
    ("gemini", "/models/fixture:generateContent"), ("claude", "/messages"), ("ollama", "/api/chat"),
])
def test_provider_changes_printed_lines(monkeypatch, provider, suffix):
    calls = []

    def answer(path, data):
        calls.append(path)
        return choose_layout(path, data)

    with model_server(answer) as port, TestClient(app, base_url="http://127.0.0.1") as client:
        base = f"http://127.0.0.1:{port}/proxy"
        monkeypatch.setenv("JJOKGYEOL_DECISION_BASE_URLS", base)
        response = client.post("/api/render", headers={"Accept": "multipart/form-data"}, json={
            "source": SOURCE, "style": STYLE,
            "connection": {"provider": provider, "base_url": base, "model": "fixture", "api_key": "local-test"},
        })
    assert response.status_code == 200, response.content[:200]
    assert response.headers["X-Jjokgyeol-Decision"] == "model"
    assert calls == ["/proxy" + suffix]
    packet = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {response.headers['Content-Type']}\r\n\r\n".encode() + response.content)
    proofs = {part.get_param("name", header="Content-Disposition"): scan(part.get_payload(decode=True))
              for part in packet.iter_parts()}
    assert proofs["rule"][0] == [9, 11]
    assert proofs["model"][0] == [7, 11]
    assert proofs["rule"][1] == proofs["model"][1]


def test_preview_downloads_printed_model_and_rule_after_applying_settings(browser, server_url, monkeypatch):
    with model_server(choose_layout) as port, browser.new_page() as page:
        base = f"http://127.0.0.1:{port}/v1"
        monkeypatch.setenv("JJOKGYEOL_DECISION_BASE_URLS", base)
        page.goto(server_url)
        expect(page.locator("#status")).to_have_text("최신 버전", timeout=45_000)
        page.locator("#source").fill(SOURCE)
        open_settings(page)
        with page.expect_response("**/api/style/open"):
            page.locator("#style-file").set_input_files({
                "name": "기준.json", "mimeType": "application/json",
                "buffer": json.dumps(from_editor_style(STYLE).to_stored()).encode(),
            })
        page.locator("#decision-mode").select_option("model")
        page.locator("#decision-provider").select_option("chat")
        page.locator("#decision-base-url").fill(base)
        page.locator("#decision-model").fill("fixture")
        page.locator("#apply-settings").click()
        with page.expect_response("**/api/render", timeout=45_000):
            page.locator("#refresh").click()
        expect(page.locator("#proof-decision")).to_contain_text("모델 조판")
        for choice, expected in (("model", [7, 11]), ("rule", [9, 11])):
            page.locator("#proof-choice").select_option(choice)
            with page.expect_download() as downloaded:
                page.locator("#export").click()
            assert scan(downloaded.value.path().read_bytes())[0] == expected
