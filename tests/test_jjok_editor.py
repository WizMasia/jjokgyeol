"""Save and reopen the displayed proof through the shared document controls."""
import base64
import hashlib
import io
import json
import re
from zipfile import ZipFile

import pytest
from playwright.sync_api import expect
from pypdf import PdfWriter

from app.font_assets import FONT_ASSETS, LOCKED_ASSET_PATHS
from settings_ui import close_settings, open_settings
from test_editor import ROOT, browser
from test_jjok_archive import STATIC, archive, entries as members, manifest
from test_reference_editor import review as editor
from test_slice import server_url


def reply_pair(desk, index, decision="model"):
    files = {}
    parts = []
    for mode, width in (("rule", 210), ("model", 211)):
        pdf = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=width, height=297)
        writer.write(pdf)
        files[mode] = pdf.getvalue()
        parts.append((f'--pair\r\nContent-Disposition: form-data; name="{mode}"; filename="{mode}.pdf"\r\n'
                      'Content-Type: application/pdf\r\n\r\n').encode() + files[mode] + b"\r\n")
    desk.pending[index].fulfill(headers={"Content-Type": "multipart/form-data; boundary=pair",
                                         "X-Jjokgyeol-Decision": decision},
                                body=b"".join(parts) + b"--pair--\r\n")
    expect(desk.page.locator("#proof-decision")).to_contain_text("혼합" if decision == "mixed" else "모델 조판")
    return files


def save_document(page):
    with page.expect_download() as saved:
        page.locator("#save-document").click()
    assert saved.value.suggested_filename.endswith(".jjok")
    return saved.value.path().read_bytes()


def entries(data):
    with ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def upload(page, selector, name, data):
    page.locator(selector).set_input_files({"name": name, "mimeType": "application/octet-stream", "buffer": data})


def proof_bytes(desk):
    return bytes(desk.frame.evaluate("async el => Array.from(new Uint8Array(await (await fetch(el.src)).arrayBuffer()))"))


def test_document_picker_original_bytes_and_per_document_pdf_preference(editor):
    p = editor.page
    expect(p.get_by_label("교정 PDF 포함", exact=True)).to_be_checked()
    source = b"\xef\xbb\xbf# Original\r\none\ntwo\r"
    upload(p, "#file", "원문.md", source)
    expect(editor.source).to_have_value("# Original\none\ntwo\n")
    saved = entries(save_document(p))
    assert saved["manuscript.md"] == source and "proof.pdf" not in saved
    editor.submit(editor.refresh)
    editor.reply(0, editor.pdf)
    open_settings(p)
    p.locator("#body_pt").fill("12.0000152587890625")
    p.locator("#paragraph-gap-pt").fill("6.25")
    close_settings(p)
    p.locator("#include-proof").uncheck()
    data = save_document(p)
    saved = entries(data)
    assert saved["manuscript.md"] == source
    assert json.loads(saved["style.json"])["body_sp"] == 786433
    assert json.loads(saved["style.json"])["paragraph_gap_sp"] == 409600
    assert "proof.pdf" not in saved and proof_bytes(editor) == editor.pdf
    with p.expect_file_chooser() as chooser:
        p.locator("#open-document").click()
    assert chooser.value.element.get_attribute("accept") == ".jjok"
    p.once("dialog", lambda dialog: dialog.accept())
    chooser.value.set_files({"name": "정본.jjok", "mimeType": "application/zip", "buffer": data})
    expect(p.locator("#preview")).not_to_have_attribute("src", re.compile(r"^blob:"))
    expect(p.locator("#include-proof")).not_to_be_checked()
    assert entries(save_document(p))["style.json"] == saved["style.json"]
    with p.expect_download() as manuscript:
        p.locator("#save").click()
    assert manuscript.value.path().read_bytes() == source
    upload(p, "#file", "새.md", b"new")
    expect(p.locator("#include-proof")).to_be_checked()
    assert len(editor.requests) == 2


@pytest.mark.parametrize(("decision", "choice", "mode"), [
    ("model", "rule", "rule"), ("model", "model", "model"), ("mixed", "model", "mixed"),
])
@pytest.mark.parametrize("stale", [False, True])
def test_reopen_selected_proof_and_resave_without_model(editor, decision, choice, mode, stale):
    p = editor.page
    editor.submit(editor.refresh)
    pair = reply_pair(editor, 0, decision)
    p.locator("#proof-choice").select_option(choice)
    if stale:
        editor.source.fill("수정한 원고")
    saved = save_document(p)
    stored = entries(saved)
    assert stored["proof.pdf"] == pair[choice]
    assert json.loads(stored["manifest.json"])["proof"] == {"mode": mode, "state": "stale" if stale else "current"}
    editor.source.fill("다른 원고")
    editor.submit(editor.export)
    p.once("dialog", lambda dialog: dialog.accept())
    upload(p, "#document-file", "교정.jjok", saved)
    expect(p.locator("#proof-choice")).to_have_value(mode)
    expect(p.locator("#status")).to_contain_text("수정 전 교정본" if stale else "최신")
    assert proof_bytes(editor) == pair[choice]
    editor.reply(1, {"message": "지난 작업 오류"}, 422)
    expect(p.locator("#error")).to_be_empty()
    assert entries(save_document(p))["proof.pdf"] == pair[choice]
    if stale:
        expect(p.locator("#applied-ratios")).to_be_empty()
    editor.submit(editor.refresh)
    editor.reply(2, editor.pdf)
    expect(p.locator("#status")).to_contain_text("최신")
    expect(p.locator("#proof-decision")).to_contain_text("규칙 조판")
    refreshed = entries(save_document(p))
    assert refreshed["proof.pdf"] == editor.pdf
    assert json.loads(refreshed["manifest.json"])["proof"] == {"mode": "rule", "state": "current"}


def test_save_freezes_source_style_preference_and_selected_blob(editor):
    p = editor.page
    editor.submit(editor.refresh)
    pair = reply_pair(editor, 0)
    editor.source.fill("저장 시점")
    stalled = []
    p.route("**/api/style/export", lambda route: stalled.append(route))
    with p.expect_request("**/api/style/export"):
        p.locator("#save-document").click()
    editor.source.fill("저장 중 입력")
    p.locator("#include-proof").uncheck()
    p.locator("#proof-choice").select_option("rule")
    open_settings(p)
    p.locator("#body_pt").fill("14")
    close_settings(p)
    editor.submit(editor.refresh)
    editor.reply(1, editor.pdf)
    with p.expect_download() as saved:
        editor.route(stalled[0])
    stored = entries(saved.value.path().read_bytes())
    assert stored["manuscript.md"].decode() == "저장 시점"
    assert json.loads(stored["style.json"])["body_sp"] == 11 * 65536
    assert stored["proof.pdf"] == pair["model"]
    p.once("dialog", lambda dialog: dialog.dismiss())
    upload(p, "#document-file", "저장본.jjok", saved.value.path().read_bytes())
    expect(p.locator("#save-document")).to_be_enabled()
    expect(editor.source).to_have_value("저장 중 입력")
    expect(p.locator("#include-proof")).not_to_be_checked()


def test_resaving_a_fallback_proof_keeps_its_displayed_faces_across_edits_and_late_work(editor):
    p = editor.page
    p.locator("#open-fonts").click()
    p.locator("#font-files").set_input_files(ROOT / "tests/fixtures/fonts/abel/Abel-Regular.ttf")
    option = p.locator("#font-family option", has_text="Abel")
    expect(option).to_have_count(1)
    family = option.get_attribute("value")
    p.locator("#font-family").select_option(family)
    p.locator("#apply-font").click()
    p.once("dialog", lambda dialog: dialog.dismiss())
    editor.submit(editor.refresh)
    pair = reply_pair(editor, 0)
    p.locator("#proof-choice").select_option("rule")
    saved = save_document(p)
    p.once("dialog", lambda dialog: dialog.accept())
    upload(p, "#document-file", "fallback.jjok", saved)
    expect(p.locator("#proof-choice")).to_be_disabled()
    expect(p.locator("#status")).to_have_text("최신 버전")

    open_settings(p)
    p.locator("#body_font").select_option("nanum-myeongjo")
    close_settings(p)
    editor.submit(editor.refresh)
    editor.source.fill("edited before the new proof arrived")
    editor.reply(1, editor.pdf)
    stored = entries(save_document(p))
    assert json.loads(stored["style.json"])["body_font"] == "nanum-myeongjo"
    assert stored["proof.pdf"] == pair["rule"]
    fallback = json.loads((ROOT / "runtime.lock").read_text())["fonts"]["nanum-myeongjo"]
    expected_faces = [fallback[weight]["sha256"] for weight in ("regular", "bold")]
    assert json.loads(stored["manifest.json"])["proof"] == {
        "state": "stale", "mode": "rule", "appliedFonts": {family: expected_faces},
    }


def test_undeclared_style_font_is_rejected_before_replacement(editor):
    p = editor.page
    editor.source.fill("unsaved manuscript")
    shown = editor.frame.get_attribute("src")
    doc = members({**manifest(), "version": 2, "fonts": []})
    style = json.loads(doc["style.json"])
    style["text_styles"]["heading1"]["font"] = "font-none-" + "a" * 64
    doc["style.json"] = json.dumps(style).encode()
    confirmations = []
    p.on("dialog", lambda dialog: (confirmations.append(dialog.message), dialog.accept()))
    upload(p, "#document-file", "incomplete.jjok", archive(doc))
    expect(p.locator("#error")).to_contain_text("글꼴")
    expect(editor.source).to_have_value("unsaved manuscript")
    assert editor.frame.get_attribute("src") == shown
    assert not confirmations


def test_explicit_empty_face_snapshot_matches_a_style_without_document_fonts(editor):
    proof = {"state": "current", "mode": "rule", "appliedFonts": {}}
    doc = members({**manifest(proof=proof), "version": 2, "fonts": []}, editor.pdf)
    upload(editor.page, "#document-file", "empty-faces.jjok", archive(doc))
    expect(editor.page.locator("#proof-choice")).to_be_disabled()
    expect(editor.page.locator("#status")).to_have_text("최신 버전")


@pytest.mark.parametrize("version,dynamic,stale", [(1, True, False), (2, True, True), (2, False, False)])
def test_earlier_proof_without_face_snapshot(editor, version, dynamic, stale):
    locked = json.loads((ROOT / "runtime.lock").read_text())["fonts"]["nanum-gothic"]["regular"]
    record = {"hash": locked["sha256"], "version": locked["version"], "name": "Gothic", "weight": "regular"}
    metadata = {**manifest(proof={"state": "current", "mode": "rule"}), "version": version}
    if version == 2:
        metadata["fonts"] = [record]
    doc = members(metadata, editor.pdf)
    if dynamic:
        doc["style.json"] = json.dumps({**json.loads(doc["style.json"]), "body_font": f"font-{record['hash']}-none"})
    upload(editor.page, "#document-file", "early.jjok", archive(doc))
    expect(editor.page.locator("#proof-choice")).to_be_disabled()
    expect(editor.page.locator("#status")).to_have_text("수정 전 교정본 · 갱신 필요" if stale else "최신 버전")


def test_previous_external_coding_face_opens_and_resaves_with_shipped_notice(editor):
    digest = "787effd7efed2abca88ade231faa8191f4e9fcf85b1805a13ee1dc3724b72089"
    source = LOCKED_ASSET_PATHS[digest]
    if not source.is_file():
        pytest.skip("requires locked NanumGothicCoding.ttf")
    font = source.read_bytes()
    holder = "Copyright © 2009 NHN Corporation. All rights reserved. Font designed by Sandoll Communications Inc."
    old_notice = holder.encode() + b"\n\n" + (STATIC / "ofl-1.1.txt").read_bytes()
    p = editor.page
    for notice in (b"Another author\n" + old_notice, old_notice):
        record = {"hash": digest, "name": "NanumGothicCoding", "weight": "regular", "version": "2.000",
                  "file": "fonts/" + digest, "notice": f"notices/{hashlib.sha256(notice).hexdigest()}.txt"}
        doc = members({**manifest(), "version": 2, "fonts": [record]})
        doc.update({"style.json": json.dumps({**json.loads(doc["style.json"]), "body_font": f"font-{digest}-none"}),
                    record["file"]: font, record["notice"]: notice})
        upload(p, "#document-file", "early-coding.jjok", archive(doc))
        if notice != old_notice:
            expect(p.locator("#error")).to_contain_text("고지")
            assert p.locator("#body_font").input_value() != f"font-{digest}-none"
    expect(p.locator("#body_font")).to_have_value(f"font-{digest}-none")
    saved = entries(save_document(p))
    face = next(row for row in json.loads(saved["manifest.json"])["fonts"] if row["hash"] == digest)
    assert saved[face["file"]] == font
    assert face["notice"] == f"notices/{FONT_ASSETS['notice']}.txt"
    assert saved[face["notice"]] == LOCKED_ASSET_PATHS[FONT_ASSETS["notice"]].read_bytes()


def test_failed_document_and_cancel_keep_every_edit_and_current_proof(editor):
    p = editor.page
    valid = save_document(p)
    editor.source.fill("미저장 원고")
    p.locator("#include-proof").uncheck()
    open_settings(p)
    p.locator("#body_pt").fill("13")
    close_settings(p)
    old_url = editor.frame.get_attribute("src")
    bad = io.BytesIO()
    with ZipFile(bad, "w") as archive:
        for name, body in entries(valid).items():
            archive.writestr(name, b'{"body_sp":-100}' if name == "style.json" else body)
    upload(p, "#document-file", "손상.jjok", bad.getvalue())
    expect(p.locator("#error")).not_to_be_empty()
    asked = []
    p.once("dialog", lambda dialog: (asked.append(dialog.message), dialog.dismiss()))
    upload(p, "#document-file", "정상.jjok", valid)
    expect(p.locator("#save-document")).to_be_enabled()
    assert asked
    expect(editor.source).to_have_value("미저장 원고")
    expect(p.locator("#body_pt")).to_have_value("13")
    expect(p.locator("#include-proof")).not_to_be_checked()
    assert editor.frame.get_attribute("src") == old_url


@pytest.mark.parametrize("newer", ["archive", "markdown", "style", "edit", "choice"])
def test_slow_document_never_overwrites_newer_file_or_edit(editor, newer):
    p = editor.page
    if newer == "choice":
        editor.submit(editor.refresh)
        reply_pair(editor, 0)
    archived = save_document(p)
    held = []
    p.route("**/api/style/open", lambda route: editor.route(route) if held else held.append(route))
    with p.expect_request("**/api/style/open"):
        upload(p, "#document-file", "지난.jjok", archived)
    if newer == "archive":
        upload(p, "#document-file", "선택.jjok", archived)
    elif newer == "choice":
        p.locator("#proof-choice").select_option("rule")
    elif newer == "markdown":
        upload(p, "#file", "선택.md", b"new markdown")
    elif newer == "style":
        open_settings(p)
        p.locator("#style summary").click()
        stored_style = json.loads(entries(archived)["style.json"])
        upload(p, "#style-file", "선택.json", json.dumps({**stored_style, "body_sp": 786432}).encode())
        close_settings(p)
    else:
        editor.source.fill("입력한 내용")
    expect(p.locator("#save-document")).to_be_enabled()
    with p.expect_request_finished(lambda request: request == held[0].request):
        held[0].fulfill(status=422, json={"message": "지난 파일 오류"})
    expect(p.locator("#error")).to_be_empty()
    if newer == "choice":
        expect(p.locator("#proof-choice")).to_have_value("rule")
    elif newer == "style":
        expect(p.locator("#body_pt")).to_have_value("12")
    elif newer == "edit":
        expect(editor.source).to_have_value("입력한 내용")
    elif newer == "markdown":
        expect(editor.source).to_have_value("new markdown")
        assert "proof.pdf" not in entries(save_document(p))
    else:
        with p.expect_download() as saved:
            p.locator("#save-document").click()
        assert saved.value.suggested_filename == "선택.jjok"


def test_restored_stale_pdf_downloads_until_an_edit_requires_new_typesetting(editor):
    p = editor.page
    editor.source.fill("아직 조판하지 않은 수정")
    saved = save_document(p)
    p.once("dialog", lambda dialog: dialog.accept())
    upload(p, "#document-file", "수정.jjok", saved)
    expect(p.locator("#status")).to_contain_text("수정 전 교정본")
    with p.expect_download() as download:
        editor.export.click()
    assert download.value.path().read_bytes() == editor.pdf and len(editor.requests) == 1
    editor.source.fill("다음 수정")
    editor.submit(editor.export)
    with p.expect_download():
        editor.reply(0, editor.pdf)


def test_unreadable_selected_pdf_cannot_be_saved_as_a_successful_proofless_document(editor):
    p = editor.page
    editor.source.fill("저장할 원고")
    editor.submit(editor.refresh)
    editor.reply(0, b"unreadable")
    shown = editor.frame.get_attribute("src")
    downloads = []
    p.on("download", lambda file: downloads.append(file))
    p.locator("#save-document").click()
    expect(p.locator("#error")).to_contain_text("PDF")
    assert not downloads and editor.frame.get_attribute("src") == shown
    p.locator("#include-proof").uncheck()
    assert "proof.pdf" not in entries(save_document(p))


def test_first_render_in_flight_and_superseded_success_cannot_supply_a_foreign_proof(editor):
    p = editor.page
    upload(p, "#file", "새.md", b"new")
    expect(editor.source).to_have_value("new")
    editor.submit(editor.refresh)
    saved = save_document(p)
    stored = entries(saved)
    assert "proof.pdf" not in stored and json.loads(stored["manifest.json"])["includeProof"] is True
    upload(p, "#document-file", "다음.jjok", saved)
    expect(p.locator("#placeholder")).to_be_visible()
    editor.reply(0, editor.pdf)
    expect(p.locator("#proof-decision")).to_be_empty()
    assert "proof.pdf" not in entries(save_document(p))


def test_rule_only_desktop_restores_foreign_model_and_keeps_edits_on_native_failure(editor, browser):
    p = editor.page
    editor.submit(editor.refresh)
    pair = reply_pair(editor, 0)
    archive = save_document(p)
    with browser.new_context() as context:
        native = context.new_page()
        native.set_default_timeout(4000)
        # Only replace platform I/O. The editor, archive and style endpoints are real.
        native.add_init_script("""
          window.__TAURI__ = {};
          window.saves = [];
          window.savedData = index => new Promise(resolve => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result.split(",")[1]);
            reader.readAsDataURL(window.saves[index].blob);
          });
          window.desktop = {
            proof: async () => { throw Error("모델 미연결"); },
            pages: async () => {
              if (window.failPages) throw Error("저장한 지면을 읽지 못했습니다.");
              const pages = document.createElement("div");
              pages.id = "desktop-pages";
              return pages;
            },
            style: async (action, body) => {
              const response = await fetch("/api/style/" + action, {method: "POST", headers: {"Content-Type": "application/json"}, body});
              if (!response.ok) throw Error("양식 오류");
              return response.json();
            },
            save: async (blob, name) => {
              window.saves.push({name, blob});
              return new Promise(resolve => window.finishSave = resolve);
            },
            setDirty: async dirty => { window.nativeDirty = dirty; },
            setStyleDirty: async () => {},
            confirmDocumentReplacement: async () => false,
            confirmStyleReplacement: async () => false,
          };
        """)
        native.route("**/*", editor.route)
        native.goto("http://127.0.0.1/")
        expect(native.locator("#status")).to_contain_text("조판 실패")
        upload(native, "#document-file", "모델.jjok", archive)
        expect(native.locator("#proof-choice")).to_have_value("model")
        expect(native.locator("#proof-decision")).to_contain_text("모델 조판")
        native.locator("#export").click()
        native.wait_for_function("window.saves.length === 1")
        assert base64.b64decode(native.evaluate("savedData(0)")) == pair["model"]
        native.evaluate("finishSave(true)")
        native.locator("#source").fill("수정")
        native.locator("#save-document").click()
        native.wait_for_function("window.saves.length === 2")
        native.evaluate("finishSave(false)")
        expect(native.locator("#save-document")).to_be_enabled()
        assert native.evaluate("window.nativeDirty")
        native.locator("#save-document").click()
        native.wait_for_function("window.saves.length === 3")
        native.locator("#source").fill("저장 중 수정")
        native.evaluate("finishSave(true)")
        expect(native.locator("#save-document")).to_be_enabled()
        assert native.evaluate("window.nativeDirty")
        stored = entries(base64.b64decode(native.evaluate("savedData(2)")))
        assert stored["proof.pdf"] == pair["model"]
        assert json.loads(stored["manifest.json"])["proof"] == {"mode": "model", "state": "stale"}
        native.evaluate("window.failPages = true")
        upload(native, "#document-file", "손상.jjok", archive)
        expect(native.locator("#error")).to_contain_text("지면")
        expect(native.locator("#source")).to_have_value("저장 중 수정")
        expect(native.locator("#proof-choice")).to_have_value("model")
        assert native.locator("#desktop-pages").count() == 1
        native.evaluate("""bytes => {
            window.failPages = false;
            desktop.proof = async () => new Blob([new Uint8Array(bytes)], {type: "application/pdf"});
        }""", list(pair["rule"]))
        native.locator("#refresh").click()
        expect(native.locator("#status")).to_contain_text("최신")
        expect(native.locator("#proof-choice")).to_have_value("rule")
        assert native.locator("#proof-choice option").count() == 1
        native.locator("#save-document").click()
        native.wait_for_function("window.saves.length === 4")
        native.evaluate("finishSave(true)")
        native.wait_for_function("window.nativeDirty === false")
        native.locator("#source").fill("다시 수정")

        native.evaluate("""() => {
            desktop.confirmDocumentReplacement = () => new Promise((resolve, reject) => {
                window.failOldConfirmation = () => reject(Error("지난 문서 확인 오류"));
            });
        }""")
        upload(native, "#document-file", "먼저.jjok", archive)
        native.wait_for_function("typeof window.failOldConfirmation === 'function'")
        native.evaluate("() => { desktop.confirmDocumentReplacement = async () => true; }")
        upload(native, "#file", "나중.md", b"later")
        expect(native.locator("#source")).to_have_value("later")
        native.evaluate("window.failOldConfirmation()")
        expect(native.locator("#error")).to_be_empty()


def test_finishing_old_save_cannot_dirty_a_new_plain_manuscript(editor):
    p = editor.page
    archive = save_document(p)
    held = []
    p.route("**/api/style/export", lambda route: held.append(route))
    with p.expect_request("**/api/style/export"):
        p.locator("#save-document").click()
    upload(p, "#file", "새 원고.md", b"new")
    expect(editor.source).to_have_value("new")
    editor.submit(editor.refresh)
    editor.reply(0, editor.pdf)
    with p.expect_download():
        editor.route(held[0])
    upload(p, "#document-file", "처음.jjok", archive)
    expect(p.locator("#proof-choice")).to_be_disabled()
    assert editor.source.input_value() != "new"


def test_style_connection_failures_identify_the_document_operation(editor):
    p = editor.page
    archive = save_document(p)
    editor.source.fill("연결 오류 뒤에도 남을 원고")
    old_url = editor.frame.get_attribute("src")
    p.route("**/api/style/*", lambda route: route.abort())
    p.locator("#save-document").click()
    expect(p.locator("#error")).to_contain_text("저장")
    upload(p, "#document-file", "다른.jjok", archive)
    expect(p.locator("#error")).to_contain_text("열지")
    expect(editor.source).to_have_value("연결 오류 뒤에도 남을 원고")
    assert editor.frame.get_attribute("src") == old_url


@pytest.mark.parametrize("changed", ["source", "style-file"])
def test_individual_files_cannot_clear_unsaved_changes_to_an_active_archive(editor, changed):
    p = editor.page
    archive = save_document(p)
    if changed == "source":
        editor.source.fill("쪽결 문서에 아직 저장하지 않은 원고")
        with p.expect_download():
            p.locator("#save").click()
    else:
        open_settings(p)
        p.locator("#style summary").click()
        stored_style = json.loads(entries(archive)["style.json"])
        upload(p, "#style-file", "별도.json", json.dumps({**stored_style, "body_sp": 786432}).encode())
        expect(p.locator("#body_pt")).to_have_value("12")
        close_settings(p)
    upload(p, "#document-file", "기존.jjok", archive)  # Playwright dismisses the unsaved-changes dialog.
    expect(p.locator("#save-document")).to_be_enabled()
    if changed == "source":
        expect(editor.source).to_have_value("쪽결 문서에 아직 저장하지 않은 원고")
    else:
        expect(p.locator("#body_pt")).to_have_value("12")


@pytest.mark.parametrize("edit", ["source", "style", "include"])
def test_browser_download_without_write_acknowledgement_keeps_dirty_guard(editor, edit):
    p = editor.page
    original = save_document(p)
    if edit == "source":
        editor.source.fill("다운로드 창에서 취소한 원고")
    elif edit == "style":
        open_settings(p)
        p.locator("#body_pt").fill("14")
        close_settings(p)
    else:
        p.locator("#include-proof").uncheck()
    with p.expect_download() as download:
        p.locator("#save-document").click()
    assert download.value.suggested_filename == "비교 원고.jjok"
    download.value.cancel()
    prompts = []
    p.once("dialog", lambda dialog: (prompts.append(dialog.message), dialog.dismiss()))
    upload(p, "#document-file", "원본.jjok", original)
    expect(p.locator("#save-document")).to_be_enabled()
    assert prompts
    if edit == "source":
        expect(editor.source).to_have_value("다운로드 창에서 취소한 원고")
    elif edit == "style":
        expect(p.locator("#body_pt")).to_have_value("14")
    else:
        expect(p.locator("#include-proof")).not_to_be_checked()


@pytest.mark.parametrize(("input_id", "name"), [("#file", "다음.md"), ("#import-file", "다음.txt")])
@pytest.mark.parametrize("pending_first_save", [False, True], ids=["opened", "pending-first-save"])
def test_plain_manuscript_replacement_confirms_dirty_web_archive(editor, input_id, name, pending_first_save):
    p = editor.page
    if pending_first_save:
        upload(p, "#file", "초고.md", b"first snapshot")
        expect(editor.source).to_have_value("first snapshot")
        held = []
        p.route("**/api/style/export", lambda route: held.append(route), times=1)
        with p.expect_request("**/api/style/export"):
            p.locator("#save-document").click()
    else:
        archive = save_document(p)
        upload(p, "#document-file", "작업.jjok", archive)
        expect(p.locator("#proof-choice")).to_be_disabled()
    editor.source.fill("교정 중인 작업 문서")
    p.route("**/api/import?*", lambda route: route.fulfill(json={"markdown": "next", "warnings": []}))
    old_pdf = editor.frame.get_attribute("src")
    prompts = []
    p.once("dialog", lambda dialog: (prompts.append(dialog.message), dialog.dismiss()))
    upload(p, input_id, name, b"next")
    expect(editor.refresh).to_be_enabled()
    assert prompts
    expect(editor.source).to_have_value("교정 중인 작업 문서")
    assert editor.frame.get_attribute("src") == old_pdf
    p.once("dialog", lambda dialog: (prompts.append(dialog.message), dialog.accept()))
    upload(p, input_id, name, b"next")
    expect(editor.source).to_have_value("next")
    assert len(prompts) == 2
    if pending_first_save:
        with p.expect_download() as first:
            editor.route(held[0])
        assert entries(first.value.path().read_bytes())["manuscript.md"] == b"first snapshot"
        expect(editor.source).to_have_value("next")
    assert "proof.pdf" not in entries(save_document(p))
