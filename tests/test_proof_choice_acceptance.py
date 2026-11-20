"""A reader can choose and export both layouts from the same production render."""
import hashlib
import os

from playwright.sync_api import expect
import pytest

from settings_ui import open_settings, close_settings
from proof_response import rendered_pdfs
from app.style import Style, apply_style
from test_decision import SOURCE, STYLE, scan
from test_refactor_acceptance import compact_json
from test_reference_acceptance import editor, url


@pytest.mark.skipif(not os.environ.get("JJOKGYEOL_DECISION_TEST_URL"), reason="requires the production decision model")
def test_selected_proof_is_the_downloaded_pdf(editor):
    open_settings(editor)
    with editor.expect_response("**/api/style/open") as opened:
        editor.locator("#style-file").set_input_files({
            "name": "조판 비교.json", "mimeType": "application/json",
            "buffer": compact_json(apply_style(Style(), STYLE).to_stored()),
        })
    assert opened.value.ok
    close_settings(editor)
    editor.locator("#source").fill(SOURCE)
    requests = []
    editor.on("request", lambda request: requests.append(request) if request.url.endswith("/api/render") else None)
    with editor.expect_response("**/api/render", timeout=45000) as reply:
        editor.locator("#refresh").click()
    response = reply.value
    assert response.ok
    assert response.headers["x-jjokgyeol-decision"] == "model"
    pdfs = rendered_pdfs(response)
    assert pdfs.keys() == {"model", "rule"}
    model, rule = scan(pdfs["model"]), scan(pdfs["rule"])
    assert model[0] != rule[0] and model[1] == rule[1]
    choice = editor.locator("#proof-choice")
    expect(choice).to_have_value("model")
    for mode in ("model", "rule", "model"):
        choice.select_option(mode)
        current = editor.locator("#preview").get_attribute("src")
        assert current.startswith("blob:")
        shown = editor.evaluate("""async url => {
            const pdf = await (await fetch(url)).arrayBuffer();
            const digest = await crypto.subtle.digest('SHA-256', pdf);
            return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
        }""", current)
        assert shown == hashlib.sha256(pdfs[mode]).hexdigest()
        with editor.expect_download() as file:
            editor.locator("#export").click()
        assert file.value.path().read_bytes() == pdfs[mode]
    assert len(requests) == 1
