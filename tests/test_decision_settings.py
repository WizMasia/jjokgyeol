"""Session decision settings through the web editor's normal controls."""
import re
from io import BytesIO
from zipfile import ZipFile

import pytest
from playwright.sync_api import expect

import test_editor
from app.configuration import CONFIG_DIR, read_config
from app.style import editor_bootstrap
from settings_ui import open_settings, close_settings
from test_editor import Desk, ORIGIN, browser, desk


def test_bootstrap_uses_provider_registry():
    assert editor_bootstrap()["decision_providers"] == read_config(CONFIG_DIR / "decision-providers.json")["providers"]


@pytest.mark.parametrize("desk", [{"operator_decision": True}], indirect=True)
def test_operator_default_can_be_overridden_and_restored(desk):
    desk.ready()
    page = desk.page
    open_settings(page)
    expect(page.locator("#decision-mode")).to_have_value("default")
    page.locator("#decision-mode").select_option("rule")
    page.locator("#apply-settings").click()
    desk.submit(desk.refresh)
    assert desk.requests[-1]["connection"] == {"provider": "rule"}
    desk.reply(0, desk.pdf)
    open_settings(page)
    page.locator("#decision-mode").select_option("default")
    page.locator("#apply-settings").click()
    desk.submit(desk.refresh)
    assert "connection" not in desk.requests[-1]
    desk.reply(1, desk.pdf)


@pytest.mark.parametrize("desk", [{"decision_providers": [], "builtin_decision": False}], indirect=True)
def test_editor_with_older_bootstrap_hides_connection_controls(desk):
    desk.ready()
    open_settings(desk.page)
    expect(desk.page.locator("#decision-settings")).to_be_hidden()


def choose_model(page, key="test-session-key"):
    page.locator("#decision-mode").select_option("model")
    page.locator("#decision-provider").select_option("chat")
    page.locator("#decision-base-url").fill("https://example.org/proxy/v1")
    page.locator("#decision-model").fill("sample")
    page.locator("#decision-api-key").fill(key)
    return {"provider": "chat", "base_url": "https://example.org/proxy/v1", "model": "sample", "api_key": key}


@pytest.mark.parametrize(("origin", "keys_allowed"), [
    ("http://editor.example.test", False),
    ("http://127.0.0.1.example.test", False),
    ("https://editor.example.test", True),
    ("http://localhost:8000", True),
    ("http://127.0.0.2:8000", True),
    ("http://[::1]:8000", True),
])
def test_key_transport_uses_editor_origin(browser, monkeypatch, origin, keys_allowed):
    monkeypatch.setattr(test_editor, "ORIGIN", origin)
    with browser.new_context() as context:
        page = context.new_page()
        desk = Desk(page)
        desk.ready()
        checks = []

        def check(route):
            checks.append(route.request.post_data_json)
            route.fulfill(json={"ok": True})

        page.route("**/api/decision/check", check)
        open_settings(page)
        page.locator("#decision-mode").select_option("model")
        page.locator("#decision-provider").select_option("chat")
        page.locator("#decision-model").fill("self-hosted")
        page.locator("#decision-base-url").fill("https://model.example.test/v1")
        key = page.locator("#decision-api-key")
        if keys_allowed:
            expect(key).to_be_enabled()
            key.fill("fixture-key")
        else:
            expect(key).to_be_disabled()
            expect(key).to_have_accessible_description(re.compile("HTTPS"))
            expect(page.locator("#decision-key-help")).to_be_visible()
            # A populated DOM field must still be ignored by check and apply.
            key.evaluate("input => { input.value = 'fixture-key'; }")
        page.locator("#decision-check").click()
        expect(page.locator("#decision-check-status")).to_contain_text("성공")
        expected = {"provider": "chat", "model": "self-hosted", "base_url": "https://model.example.test/v1",
                    "api_key": "fixture-key" if keys_allowed else ""}
        assert checks == [expected]
        page.locator("#apply-settings").click()
        desk.submit(desk.refresh)
        assert desk.requests[-1]["connection"] == expected
        desk.reply(0, desk.pdf)
        if not keys_allowed:
            # Even with a key in applied state, an unchanged draft stays keyless
            # and the render boundary refuses to send that state.
            open_settings(page)
            page.evaluate("decisionConnection.api_key = 'fixture-key'")
            page.locator("#decision-check").click()
            expect(page.locator("#decision-check-status")).to_contain_text("성공")
            assert checks == [expected, expected]
            close_settings(page)
            desk.refresh.click()
            expect(page.locator("#error")).to_contain_text("HTTPS")
            assert len(desk.requests) == 2


def test_apply_cancel_and_key_scope(desk):
    desk.ready()
    page = desk.page
    checks = []
    page.route("**/api/decision/check", lambda route: checks.append(route))
    open_settings(page)
    expect(page.locator("#decision-mode")).to_have_value("rule")
    connection = choose_model(page)
    assert not checks and len(desk.requests) == 1
    with page.expect_request(ORIGIN + "/api/decision/check"):
        page.locator("#decision-check").click()
    assert checks[0].request.post_data_json == connection
    page.locator("#decision-model").fill("renamed")
    checks.pop().fulfill(status=401, json={"code": "authentication", "message": "test-session-key"})
    expect(page.locator("#decision-check-status")).to_be_empty()
    expect(page.locator("#decision-api-key")).to_have_value("test-session-key")
    page.locator("#decision-model").fill("sample")
    with page.expect_request(ORIGIN + "/api/decision/check"):
        page.locator("#decision-check").click()
    checks.pop().fulfill(status=401, json={"code": "authentication", "message": "test-session-key"})
    expect(page.locator("#decision-check-status")).to_contain_text("인증")
    expect(page.locator("#decision-check-status")).not_to_contain_text("test-session-key")
    page.locator("#apply-settings").click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    assert len(desk.requests) == 1
    assert page.evaluate("[manuscriptDirty, styleDirty, documentDirty]") == [False] * 3
    desk.submit(desk.refresh)
    assert desk.requests[-1]["connection"] == connection
    desk.reply(0, desk.pdf)
    open_settings(page)
    expect(page.locator("#decision-api-key")).to_have_value("test-session-key")
    page.locator("#decision-base-url").fill("https://other.example/v1")
    expect(page.locator("#decision-api-key")).to_have_value("")
    page.locator("#decision-api-key").fill("draft")
    page.locator("#decision-provider").select_option("claude")
    expect(page.locator("#decision-api-key")).to_have_value("")
    close_settings(page)
    open_settings(page)
    expect(page.locator("#decision-base-url")).to_have_value(connection["base_url"])
    page.locator("#decision-clear-key").click()
    page.locator("#apply-settings").click()
    desk.submit(desk.refresh)
    assert desk.requests[-1]["connection"] == {**connection, "api_key": ""}
    desk.reply(1, desk.pdf)
    assert not checks


def test_latest_settings_win_without_serializing_credentials(desk):
    desk.ready()
    page = desk.page
    visible = desk.frame.get_attribute("src")
    open_settings(page)
    connection = choose_model(page)
    page.locator("#apply-settings").click()
    desk.submit(desk.refresh)
    open_settings(page)
    page.locator("#decision-mode").select_option("rule")
    page.locator("#apply-settings").click()
    desk.pending[0].fulfill(content_type="application/pdf", headers={"X-Jjokgyeol-Decision-Reason": "timeout"}, body=desk.pdf)
    expect(desk.frame).to_have_attribute("src", visible)
    expect(page.locator("#status")).to_contain_text("갱신 필요")
    desk.submit(desk.refresh)
    assert desk.requests[-1]["connection"] == {"provider": "rule"}
    desk.reply(1, desk.pdf)
    open_settings(page)
    choose_model(page)
    page.locator("#apply-settings").click()
    desk.submit(desk.refresh)
    desk.pending[2].fulfill(content_type="application/pdf", headers={"X-Jjokgyeol-Decision": "rule",
                            "X-Jjokgyeol-Decision-Reason": "timeout"}, body=desk.pdf)
    expect(page.locator("#proof-decision")).to_contain_text("시간 초과")
    page.route("**/api/style/export", lambda route: route.fulfill(json={"body_sp": 720896}))
    with page.expect_download() as saved:
        page.locator("#save-document").click()
    with ZipFile(BytesIO(saved.value.path().read_bytes())) as archive:
        for name in archive.namelist():
            data = archive.read(name)
            assert all(connection[field].encode() not in data for field in ("api_key", "base_url"))
    assert page.evaluate("[localStorage.length, sessionStorage.length]") == [0, 0]
    other = Desk(page.context.new_page())
    other.ready()
    open_settings(other.page)
    expect(other.page.locator("#decision-mode")).to_have_value("rule")
    other.page.close()


def test_builtin_mode_checks_and_renders_with_only_its_id(desk):
    desk.ready()
    page = desk.page
    page.route("**/api/decision/check", lambda route: route.fulfill(status=422, json={"code": "builtin_unavailable"}))
    open_settings(page)
    choose_model(page)
    mode = page.locator("#decision-mode")
    expect(mode.locator('option[value="builtin"]')).to_have_count(1)
    mode.select_option("builtin")
    expect(page.locator("#decision-fields")).to_be_hidden()
    with page.expect_request(ORIGIN + "/api/decision/check") as probe:
        page.locator("#decision-check").click()
    assert probe.value.post_data_json == {"provider": "builtin"}
    expect(page.locator("#decision-check-status")).to_contain_text("내장 모델")
    page.locator("#apply-settings").click()
    desk.submit(desk.refresh)
    assert desk.requests[-1]["connection"] == {"provider": "builtin"}
    desk.reply(0, desk.pdf)
    open_settings(page)
    expect(mode).to_have_value("builtin")
    mode.select_option("model")
    expect(page.locator("#decision-api-key")).to_have_value("")


@pytest.mark.parametrize("desk", [{"decision_providers": []}], indirect=True)
def test_builtin_can_be_used_without_any_external_providers(desk):
    desk.ready()
    open_settings(desk.page)
    mode = desk.page.locator("#decision-mode")
    expect(mode).to_be_visible()
    expect(mode.locator('option[value="model"]')).to_have_count(0)
    mode.select_option("builtin")
    expect(desk.page.locator("#decision-check")).to_be_visible()
