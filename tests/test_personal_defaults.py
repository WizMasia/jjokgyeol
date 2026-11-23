"""Personal defaults through the settings controls and the first render request."""
import json
from io import BytesIO
from zipfile import ZipFile

import pytest
from playwright.sync_api import expect

from app.style import Style
from settings_ui import open_settings
from test_editor import ROOT, browser
from test_slice import server_url


SAVE = "새 문서 기본값으로 저장"
RESTORE = "쪽결 기본값으로 복원"
STATUS = "#personal-defaults-status"
RULE = {"provider": "rule"}
KEY = "fixture-session-key"
MODEL = {"provider": "chat", "model": "personal-model", "base_url": "https://example.org/proxy/v1"}


@pytest.fixture
def editor(browser, server_url):
    with browser.new_context() as context:
        page = context.new_page()
        page.set_default_timeout(2500)
        requests = []

        def render(route):
            requests.append(route.request.post_data_json)
            route.fulfill(content_type="application/pdf", path=ROOT / "proofs/engine-comparison/LuaLaTeX.pdf")

        page.route("**/api/render", render)
        with page.expect_response("**/api/render"):
            page.goto(server_url)
        yield page, requests


def choose_connection(page):
    page.locator("#decision-mode").select_option("model")
    page.locator("#decision-provider").select_option(MODEL["provider"])
    page.locator("#decision-base-url").fill(MODEL["base_url"])
    page.locator("#decision-model").fill(MODEL["model"])
    page.locator("#decision-api-key").fill(KEY)


def save_defaults(page):
    with page.expect_response("**/api/style/export") as response:
        page.get_by_role("button", name=SAVE).click()
    assert response.value.ok
    expect(page.locator(STATUS)).to_contain_text("저장했습니다")


def reload_for_render(page):
    with page.expect_response("**/api/render"):
        page.reload()


def dirty_state(page):
    return page.evaluate("[manuscriptDirty, styleDirty, documentDirty, version, styleRevision]")


def test_draft_round_trip_restore_and_clean_first_preview(editor):
    page, requests = editor
    open_settings(page)
    page.locator("#body_pt").fill("13.125")
    choose_connection(page)
    original = dirty_state(page)
    save_defaults(page)
    assert dirty_state(page) == original
    assert page.evaluate("decisionConnection") == RULE
    assert len(requests) == 1
    raw = page.evaluate("Object.values(localStorage)")
    assert len(raw) == 1 and KEY not in raw[0]
    saved = json.loads(raw[0])
    assert saved["style"]["body_sp"] == 860160
    assert saved["connection"] == MODEL
    reload_for_render(page)
    assert len(requests) == 2 and requests[-1]["style"]["body_pt"] == "13.125"
    assert requests[-1]["connection"] == MODEL
    assert dirty_state(page)[:3] == [False, False, False]
    open_settings(page)
    expect(page.locator("#decision-api-key")).to_have_value("")
    original = dirty_state(page)
    page.get_by_role("button", name=RESTORE).click()
    expect(page.locator(STATUS)).to_contain_text("복원했습니다")
    assert dirty_state(page) == original
    expect(page.locator("#body_pt")).to_have_value("13.125")
    assert page.evaluate("decisionConnection") == MODEL
    reload_for_render(page)
    assert requests[-1]["style"]["body_pt"] == "11"
    assert [row["expr"] for row in requests[-1]["style"]["list_levels"]] == ["ㅁ", "ㅇ", "-"]
    assert requests[-1]["connection"] == RULE


def test_invalid_drafts_and_storage_errors_preserve_last_save(editor):
    page, _ = editor
    open_settings(page)
    save_defaults(page)
    previous = page.evaluate("Object.values(localStorage)")
    page.locator("#body_pt").fill("0")
    page.get_by_role("button", name=SAVE).click()
    expect(page.locator(STATUS)).to_contain_text("저장하지 못했습니다")
    assert page.evaluate("Object.values(localStorage)") == previous
    page.locator("#body_pt").fill("14")
    choose_connection(page)
    for url in ["https://user:pass@example.org/v1", "https://example.org/?key=" + KEY,
                "https://example.org/#private", "https://example.org/%3Fapi_key=private",
                "https://example.org/" + KEY]:
        page.locator("#decision-base-url").fill(url)
        page.locator("#decision-api-key").fill(KEY)
        page.get_by_role("button", name=SAVE).click()
        expect(page.locator(STATUS)).to_contain_text("저장하지 못했습니다")
        assert page.evaluate("Object.values(localStorage)") == previous
    page.locator("#decision-base-url").fill(MODEL["base_url"])
    page.evaluate("""() => {
        Storage.prototype.setItem = Storage.prototype.removeItem = () => { throw new Error('quota'); };
    }""")
    page.get_by_role("button", name=SAVE).click()
    expect(page.locator(STATUS)).to_contain_text("저장하지 못했습니다")
    page.get_by_role("button", name=RESTORE).click()
    expect(page.locator(STATUS)).to_contain_text("복원하지 못했습니다")
    assert page.evaluate("Object.values(localStorage)") == previous


def test_operator_is_an_explicit_persisted_choice(editor, monkeypatch):
    page, requests = editor
    monkeypatch.setenv("JJOKGYEOL_DECISION_HOST", "127.0.0.1")
    reload_for_render(page)
    assert requests[-1].get("connection") == RULE
    open_settings(page)
    expect(page.locator("#decision-mode")).to_have_value("rule")
    page.evaluate("""() => {
        window.changes = [];
        document.addEventListener('decision-connection-changed', () => changes.push(decisionConnection.provider));
    }""")
    page.locator("#decision-mode").select_option("default")
    save_defaults(page)
    assert page.evaluate("changes") == []
    page.locator("#apply-settings").click()
    assert page.evaluate("changes") == ["operator"]
    reload_for_render(page)
    assert "connection" not in requests[-1]
    open_settings(page)
    expect(page.locator("#decision-mode")).to_have_value("default")
    assert page.evaluate("decisionConnection") == {"provider": "operator"}


@pytest.mark.parametrize("replacement", ["style", "document", "edit", "markdown"])
def test_late_defaults_do_not_replace_newer_style(editor, replacement):
    page, requests = editor
    open_settings(page)
    page.locator("#body_pt").fill("13.125")
    save_defaults(page)
    held = []

    def delay_default(route):
        if not held:
            held.append(route)
        else:
            route.continue_()

    page.route("**/api/style/open", delay_default)
    with page.expect_request("**/api/style/open"):
        page.reload()
    assert len(requests) == 1
    old_style = json.dumps(Style().to_stored()).encode()
    if replacement == "document":
        data = BytesIO()
        with ZipFile(data, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"format": "jjokgyeol", "version": 1, "includeProof": False}))
            archive.writestr("style.json", old_style)
            archive.writestr("manuscript.md", "이전 문서")
        page.locator("#document-file").set_input_files({"name": "old.jjok", "mimeType": "application/zip",
                                                       "buffer": data.getvalue()})
        expect(page.locator("#source")).to_have_value("이전 문서")
    elif replacement == "markdown":
        page.locator("#file").set_input_files({"name": "new.md", "mimeType": "text/markdown", "buffer": b"new"})
        expect(page.locator("#source")).to_have_value("new")
    else:
        open_settings(page)
        if replacement == "style":
            page.locator("#style-file").set_input_files({"name": "old.json", "mimeType": "application/json",
                                                        "buffer": old_style})
            expect(page.locator("#paragraph-layout")).to_have_value("")
        else:
            page.locator("#body_pt").fill("14")
    held[0].fulfill(response=held[0].fetch())
    page.evaluate("() => new Promise(requestAnimationFrame)")
    expect(page.locator("#body_pt")).to_have_value("14" if replacement == "edit" else "11")
    if replacement in ("style", "document"):
        expect(page.locator("#paragraph-layout")).to_have_value("")
        assert "list_levels" not in page.evaluate("styleForm.get()")


@pytest.mark.parametrize("invalid", ["json", "style", "address", "denied"])
def test_unreadable_defaults_fall_back_and_report_in_settings(editor, invalid):
    page, requests = editor
    open_settings(page)
    save_defaults(page)
    if invalid == "denied":
        page.add_init_script("Storage.prototype.getItem = () => { throw new Error('blocked'); }")
    else:
        stored = json.loads(page.evaluate("Object.values(localStorage)[0]"))
        stored["connection"] = MODEL.copy()
        if invalid == "style":
            stored["style"]["body_sp"] = 0
        if invalid == "address":
            stored["connection"]["base_url"] = "https://user:secret@example.org/v1"
        raw = "{" if invalid == "json" else json.dumps(stored)
        page.evaluate("value => localStorage.setItem(localStorage.key(0), value)", raw)
    reload_for_render(page)
    assert requests[-1]["style"]["body_pt"] == "11" and requests[-1]["connection"] == RULE
    open_settings(page)
    expect(page.locator(STATUS)).to_contain_text("불러오지 못했습니다")


def test_only_manual_model_ids_follow_the_saved_endpoint(editor):
    page, requests = editor
    open_settings(page)
    choose_connection(page)
    page.locator("#decision-manual-models summary").click()
    page.locator("#decision-model-lines").fill("manual-one\nmanual-two")
    page.route("**/api/decision/models", lambda route: route.fulfill(json={"models": ["fetched-only"]}))
    page.locator("#decision-list-models").click()
    expect(page.locator("#decision-model-list option", has_text="fetched-only")).to_have_count(1)
    save_defaults(page)
    saved = json.loads(page.evaluate("Object.values(localStorage)[0]"))
    assert saved["model_ids"] == ["manual-one", "manual-two"]
    assert "fetched-only" not in json.dumps(saved)
    reload_for_render(page)
    assert requests[-1]["connection"] == MODEL and "model_ids" not in requests[-1]
    open_settings(page)
    expect(page.locator("#decision-model-lines")).to_have_value("manual-one\nmanual-two")
    expect(page.locator("#decision-model-list option", has_text="fetched-only")).to_have_count(0)
    page.locator("#decision-base-url").fill("https://other.example.org/v1")
    page.locator("#apply-settings").click()
    open_settings(page)
    expect(page.locator("#decision-model-lines")).to_have_value("")


def test_late_default_validation_preserves_applied_connection(editor):
    page, _ = editor
    open_settings(page)
    choose_connection(page)
    save_defaults(page)
    held = []
    page.route("**/api/style/open", lambda route: held.append(route))
    with page.expect_request("**/api/style/open"):
        page.reload()
    open_settings(page)
    page.locator("#decision-mode").select_option("builtin")
    page.locator("#apply-settings").click()
    held[0].fulfill(response=held[0].fetch())
    expect(page.locator("#refresh")).to_be_enabled()
    assert page.evaluate("decisionConnection") == {"provider": "builtin"}


def test_late_save_cannot_undo_restore(editor):
    page, _ = editor
    open_settings(page)
    save_defaults(page)
    held = []
    page.route("**/api/style/export", lambda route: held.append(route))
    with page.expect_request("**/api/style/export"):
        page.get_by_role("button", name=SAVE).click()
    page.get_by_role("button", name=RESTORE).click()
    with page.expect_response("**/api/style/export"):
        held[0].fulfill(response=held[0].fetch())
    page.evaluate("() => new Promise(requestAnimationFrame)")
    assert page.evaluate("localStorage.length") == 0
    expect(page.locator(STATUS)).to_contain_text("복원했습니다")


@pytest.mark.parametrize("edit_style", [False, True], ids=["keep-defaults", "edit-style"])
def test_typing_before_all_editor_scripts_load_preserves_work(editor, edit_style):
    page, requests = editor
    open_settings(page)
    page.locator("#body_pt").fill("13.125")
    choose_connection(page)
    save_defaults(page)
    scripts, samples = [], []
    page.route("**/static/style-file.js", lambda route: scripts.append(route))
    page.route("**/sample.md", lambda route: samples.append(route))
    with page.expect_request("**/sample.md"):
        page.reload(wait_until="commit")
    source = page.locator("#source")
    expect(source).to_have_value("")
    source.fill("typed during startup")
    if edit_style:
        open_settings(page)
        page.locator("#body_pt").fill("14")
        page.locator("#close-settings").click()
    samples[0].fulfill(response=samples[0].fetch())
    scripts[0].fulfill(response=scripts[0].fetch())
    page.wait_for_load_state()
    with page.expect_response("**/api/render"):
        page.locator("#refresh").click()
    assert requests[-1]["source"] == "typed during startup"
    assert requests[-1]["style"]["body_pt"] == ("14" if edit_style else "13.125")
    if not edit_style:
        assert requests[-1]["connection"] == MODEL
    assert dirty_state(page)[:3] == [True, edit_style, False]


def test_text_typed_before_editor_initializes_gets_personal_defaults(editor):
    page, requests = editor
    open_settings(page)
    page.locator("#body_pt").fill("13.125")
    choose_connection(page)
    save_defaults(page)
    held = []
    page.route("**/static/editor.js", lambda route: held.append(route))
    with page.expect_request("**/static/editor.js"):
        page.reload(wait_until="commit")
    page.locator("#source").fill("first keystrokes")
    held[0].fulfill(response=held[0].fetch())
    page.wait_for_load_state()
    with page.expect_response("**/api/render"):
        page.locator("#refresh").click()
    assert requests[-1]["source"] == "first keystrokes"
    assert requests[-1]["style"]["body_pt"] == "13.125"
    assert requests[-1]["connection"] == MODEL
    assert dirty_state(page)[:3] == [True, False, False]


def test_first_source_edit_keeps_loading_personal_style_and_connection(editor):
    page, requests = editor
    open_settings(page)
    page.locator("#body_pt").fill("13.125")
    choose_connection(page)
    save_defaults(page)
    held = []
    page.route("**/api/style/open", lambda route: held.append(route))
    with page.expect_request("**/api/style/open"):
        page.reload()
    source = page.locator("#source")
    source.fill("내가 쓰기 시작한 원고")
    with page.expect_response("**/api/style/open"):
        held[0].fulfill(response=held[0].fetch())
    expect(page.locator("#refresh")).to_be_enabled()
    with page.expect_response("**/api/render"):
        page.locator("#refresh").click()
    assert requests[-1]["source"] == "내가 쓰기 시작한 원고"
    assert requests[-1]["style"]["body_pt"] == "13.125"
    assert requests[-1]["connection"] == MODEL
    assert dirty_state(page)[:3] == [True, False, False]


def test_invalid_gemini_id_preserves_saved_defaults(editor):
    page, _ = editor
    open_settings(page)
    save_defaults(page)
    previous = page.evaluate("Object.values(localStorage)")
    page.locator("#decision-mode").select_option("model")
    page.locator("#decision-provider").select_option("gemini")
    page.locator("#decision-model").fill("two words")
    page.get_by_role("button", name=SAVE).click()
    expect(page.locator(STATUS)).to_contain_text("저장하지 못했습니다")
    assert page.evaluate("Object.values(localStorage)") == previous
    page.locator("#decision-model").fill("models/gemini-2.5-flash")
    save_defaults(page)
    saved = json.loads(page.evaluate("Object.values(localStorage)[0]"))
    assert saved["connection"]["model"] == "models/gemini-2.5-flash"
