"""Select and download the same proof in Chromium, including reference-style trials."""
import json

import pytest
from playwright.sync_api import expect

from settings_ui import open_settings, close_settings

from test_editor import DEFAULTS, ORIGIN, SAMPLE, browser, desk
from test_reference_editor import review, server_url


def reply_pair(desk, index, decision="model", fields=("rule", "model")):
    route = desk.pending[index]
    files = {name: desk.pdf + f"\n% {index} {name}\n".encode() for name in fields}
    body = b"".join(
        (f'--proof\r\nContent-Disposition: form-data; name="{name}"; filename="{name}.pdf"\r\n'
         'Content-Type: application/pdf\r\n\r\n').encode() + data + b"\r\n"
        for name, data in files.items()
    ) + b"--proof--\r\n"
    with desk.page.expect_request_finished(lambda request: request == route.request):
        route.fulfill(headers={"Content-Type": "multipart/form-data; boundary=proof",
                               "X-Jjokgyeol-Decision": decision}, body=body)
    return files


def choice(desk):
    return desk.page.get_by_role("combobox", name="조판 방식", exact=True)


def assert_download(desk, expected):
    desk.export.scroll_into_view_if_needed()
    with desk.page.expect_download() as saved:
        desk.export.click()
    assert saved.value.url == desk.page.locator("#preview").get_attribute("src")
    assert saved.value.path().read_bytes() == expected


def assert_revoked(page, *urls):
    for url in urls:
        assert page.evaluate("url => fetch(url).then(() => false, () => true)", url)


def test_model_first_switch_locally_and_keep_choice_on_next_export(desk):
    desk.ready()
    select = choice(desk)
    expect(select).to_have_attribute("id", "proof-choice")
    expect(select).to_have_value("model")
    status = desk.page.locator("#proof-decision")
    expect(status).to_contain_text("규칙")
    desk.submit(desk.refresh)
    assert desk.pending[0].request.headers["accept"] == "multipart/form-data"
    assert desk.requests[-1] == {"connection": {"provider": "rule"}, "source": SAMPLE, "style": DEFAULTS}
    pair = reply_pair(desk, 0)
    expect(status).to_contain_text("모델 조판")
    model = desk.frame.get_attribute("src")
    assert_download(desk, pair["model"])
    select.select_option("rule")
    expect(status).to_contain_text("규칙 조판")
    rule = desk.frame.get_attribute("src")
    assert_download(desk, pair["rule"])
    assert model != rule and len(desk.requests) == 2

    open_settings(desk.page)
    desk.page.get_by_label("본문 크기").fill("14")
    close_settings(desk.page)
    desk.submit(desk.export)
    with desk.page.expect_download() as saved:
        updated = reply_pair(desk, 1, "mixed")
    assert saved.value.path().read_bytes() == updated["rule"]
    expect(select).to_have_value("rule")
    expect(status).to_contain_text("규칙 조판")
    assert_revoked(desk.page, model, rule)
    select.select_option("model")
    expect(status).to_contain_text("일부 문단")
    assert_download(desk, updated["model"])
    assert desk.requests[-1] == {"connection": {"provider": "rule"}, "source": SAMPLE, "style": {**DEFAULTS, "body_pt": "14"}}
    assert len(desk.requests) == 3


def test_fallback_keeps_model_preference_and_recovers(desk):
    desk.ready()
    open_settings(desk.page)
    desk.page.locator("#decision-mode").select_option("builtin")
    desk.page.locator("#apply-settings").click()
    desk.submit(desk.refresh)
    body = desk.pdf + b"\n% rule only\n"
    assert desk.requests[-1]["connection"] == {"provider": "builtin"}
    desk.pending[0].fulfill(content_type="application/pdf", headers={
        "X-Jjokgyeol-Decision": "rule", "X-Jjokgyeol-Decision-Reason": "timeout",
    }, body=body)
    status = desk.page.locator("#proof-decision")
    expect(status).to_contain_text("응답 시간 초과")
    expect(choice(desk)).to_have_value("model")
    assert_download(desk, body)
    desk.submit(desk.refresh)
    pair = reply_pair(desk, 1)
    expect(status).to_contain_text("모델 조판")
    assert_download(desk, pair["model"])


@pytest.mark.parametrize("broken", ["missing-rule", "bad-envelope"])
def test_failed_pair_and_superseded_export_preserve_last_good_pair(desk, broken):
    desk.ready()
    desk.submit(desk.refresh)
    reply_pair(desk, 0)
    expect(desk.page.locator("#proof-decision")).to_contain_text("모델 조판")
    model = desk.frame.get_attribute("src")
    choice(desk).select_option("rule")
    rule = desk.frame.get_attribute("src")
    downloads = []
    desk.page.on("download", lambda item: downloads.append(item))
    desk.source.fill("이전 원고")
    desk.submit(desk.export)
    desk.source.fill("다음 원고")
    desk.submit(desk.refresh)
    if broken == "missing-rule":
        reply_pair(desk, 2, fields=("model",))
    else:
        desk.pending[2].fulfill(content_type="multipart/form-data; boundary=missing",
                                headers={"X-Jjokgyeol-Decision": "model"}, body=b"truncated")
    expect(desk.page.get_by_role("status")).to_contain_text("이전 버전 · 조판 실패")
    reply_pair(desk, 1, "mixed")
    assert desk.frame.get_attribute("src") == rule
    choice(desk).select_option("model")
    assert desk.frame.get_attribute("src") == model
    expect(desk.page.get_by_role("status")).to_contain_text("이전 버전 · 조판 실패")
    expect(desk.page.get_by_role("alert")).to_contain_text("PDF")
    assert not downloads and len(desk.requests) == 4


def test_opening_files_drops_pending_export_and_retains_choice(desk):
    desk.ready()
    desk.submit(desk.refresh)
    reply_pair(desk, 0, "mixed")
    expect(desk.page.locator("#proof-decision")).to_contain_text("일부 문단")
    choice(desk).select_option("rule")
    previous = desk.frame.get_attribute("src")
    downloads = []
    desk.page.on("download", lambda item: downloads.append(item))
    desk.source.fill("편집 중")
    desk.submit(desk.export)
    opening = []
    desk.page.route(ORIGIN + "/api/style/open", lambda route: opening.append(route))
    open_settings(desk.page)
    with desk.page.expect_request(ORIGIN + "/api/style/open"):
        desk.page.locator("#style-file").set_input_files({"name": "서식.json", "mimeType": "application/json",
                                                         "buffer": b"{}"})
    reply_pair(desk, 1)
    expect(desk.export).to_be_disabled()
    assert desk.frame.get_attribute("src") == previous and not downloads
    opening[0].fulfill(json={**DEFAULTS, "body_pt": "12"})
    expect(desk.export).to_be_enabled()
    close_settings(desk.page)
    desk.page.locator("#file").set_input_files({"name": "새.md", "mimeType": "text/markdown", "buffer": b"# New"})
    expect(desk.source).to_have_value("# New")
    expect(choice(desk)).to_have_value("rule")
    desk.submit(desk.export)
    with desk.page.expect_download() as saved:
        updated = reply_pair(desk, 2)
    assert desk.requests[-1] == {"connection": {"provider": "rule"}, "source": "# New", "style": {**DEFAULTS, "body_pt": "12"}}
    assert saved.value.suggested_filename == "새.pdf" and saved.value.path().read_bytes() == updated["rule"]


def test_choice_during_export_stays_out_of_saved_files(review):
    d, p = review, review.page
    source = "# 선택을 저장하지 않는 원고"
    d.source.fill(source)
    d.submit(d.export)
    choice(d).select_option("rule")
    with p.expect_download() as pdf:
        pair = reply_pair(d, 0)
    assert pdf.value.path().read_bytes() == pair["rule"]
    assert pdf.value.url == d.frame.get_attribute("src")

    with p.expect_download() as manuscript:
        p.locator("#save").click()
    assert manuscript.value.path().read_text(encoding="utf-8") == source
    open_settings(p)
    p.locator("#style summary").click()
    with p.expect_request(ORIGIN + "/api/style/export") as request, p.expect_download() as saved:
        p.locator("#save-style").click()
    assert request.value.post_data_json == DEFAULTS
    expected = p.request.post(d.service + "/api/style/export", data=DEFAULTS)
    assert expected.ok and json.loads(saved.value.path().read_bytes()) == expected.json()
    assert len(d.requests) == 2


def test_trial_switch_cancel_then_apply_transfers_both_proofs(review):
    d, p = review, review.page
    select = choice(d)
    trial = p.locator("#reference-trial")
    link = p.get_by_role("link", name="시험 PDF 열기")
    main = p.locator("#preview")
    original = main.get_attribute("src")
    for index in (0, 1):
        d.open()
        d.analyze()
        d.choose()
        d.submit(trial)
        assert d.pending[index].request.headers["accept"] == "multipart/form-data"
        pair = reply_pair(d, index, "mixed")
        expect(link).to_be_visible()
        close_settings(p)
        select.select_option("model")
        open_settings(p)
        expect(p.locator("#reference-proof")).to_contain_text("일부 문단")
        model = link.get_attribute("href")
        close_settings(p)
        select.select_option("rule")
        open_settings(p)
        expect(p.locator("#reference-proof")).to_contain_text("규칙 조판")
        rule = link.get_attribute("href")
        if p.evaluate("navigator.pdfViewerEnabled"):
            expect(p.get_by_title("시험 조판 미리보기")).to_have_attribute("src", rule)
        assert model != rule and main.get_attribute("src") == original
        if index == 0:
            p.locator("#reference-cancel").click()
            assert_revoked(p, model, rule)
            close_settings(p)
            assert_download(d, d.pdf)
    close_settings(p)
    d.submit(d.refresh)
    d.export.click()  # Joins the pending refresh of the style before apply.
    unwanted = []
    p.on("download", lambda item: unwanted.append(item))
    open_settings(p)
    p.locator("#reference-apply").click()
    reply_pair(d, 2)
    close_settings(p)
    assert not unwanted and main.get_attribute("src") == rule
    assert_download(d, pair["rule"])
    select.select_option("model")
    assert main.get_attribute("src") == model
    expect(p.locator("#proof-decision")).to_contain_text("일부 문단")
    assert_download(d, pair["model"])
    assert_revoked(p, original)
    assert len(d.requests) == 4
