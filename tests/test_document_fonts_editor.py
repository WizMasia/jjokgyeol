"""Check the editor boundary between local selection, proof rendering and archives."""
import hashlib
import json

import pytest
from playwright.sync_api import expect

from app import style
from settings_ui import open_settings
from test_editor import Desk, ORIGIN, browser


REGULAR = b"regular"
BOLD = b"bold"
OTHER = b"unused"
REG = hashlib.sha256(REGULAR).hexdigest()
B = hashlib.sha256(BOLD).hexdigest()
FAMILY = f"font-{REG}-{B}"


def files(*contents):
    return [{"name": data.decode() + ".otf", "mimeType": "font/otf", "buffer": data} for data in contents]


@pytest.fixture
def fonts(browser, monkeypatch):
    config = style.editor_bootstrap()
    config["font_assets"] = {"faces": {}, "notice": "0" * 64}
    config["document_limits"].update(font_file=1000, font_total=4096, font_count=10)
    monkeypatch.setattr(style, "editor_bootstrap", lambda: config)
    with browser.new_context() as context:
        page = context.new_page()
        page.set_default_timeout(2500)
        desk = Desk(page)
        desk.ready()
        page.evaluate("""() => {
          window.FontRights = {
            async inspect(blob) {
              const data = await blob.text();
              if (data === "slow") await new Promise(resolve => window.finishInspect = resolve);
              const bytes = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
              const hash = Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2,"0")).join("");
              return {hash, name: data === "unused" ? "Other" : "Paper", version:"1",
                weight: data === "bold" ? "bold" : "regular", copyright:"Example", license:"OFL",
                url:"https://example.org/license", pdf:true, archive:true, remote:true, reason:"OFL 1.1"};
            }, notice: () => new Blob(["OFL notice"])
          };
        }""")
        yield desk


def picker(page):
    page.locator("#open-fonts").click()


def choose_pair(page):
    picker(page)
    page.locator("#font-files").set_input_files(files(OTHER, REGULAR, BOLD))
    expect(page.locator("#font-family option", has_text="Paper")).to_have_count(1)
    page.locator("#font-family").select_option(FAMILY)
    page.locator("#apply-font").click()
    expect(page.locator("#body_font")).to_have_value(FAMILY)


@pytest.mark.parametrize("consent", [True, False])
def test_selected_pair_only_in_frame_and_consent_once(fonts, consent):
    p = fonts.page
    choose_pair(p)
    assert len(fonts.requests) == 1
    open_settings(p)
    expect(p.locator("#heading_font option", has_text="Paper")).to_have_count(1)
    expect(p.locator('[data-text="font"] option', has_text="Paper")).to_have_count(1)
    p.locator("#close-settings").click()
    calls, prompts = [], []
    p.route("**/api/render", lambda route: (
        calls.append((route.request.headers["content-type"], route.request.post_data_buffer)),
        route.fulfill(content_type="application/pdf", body=fonts.pdf)))
    def answer(dialog):
        prompts.append(dialog.message)
        dialog.accept() if consent else dialog.dismiss()
    p.on("dialog", answer)
    for _ in range(2):
        with p.expect_request("**/api/render"):
            fonts.refresh.click()
    assert len(prompts) == 1 and ORIGIN in prompts[0]
    for kind, data in calls:
        if consent:
            assert kind == "application/x-jjok-font-render"
            end = 4 + int.from_bytes(data[:4], "big")
            info = json.loads(data[4:end])
            assert info["request"]["style"]["body_font"] == FAMILY
            assert info["faces"] == [{"hash": REG, "size": 7}, {"hash": B, "size": 4}]
            assert data[end:] == REGULAR + BOLD
        else:
            assert kind == "application/json"
            assert json.loads(data)["style"]["body_font"] == FAMILY
            expect(p.locator("#font-fallback")).to_contain_text("Paper")


def test_device_list_permission_and_late_inspection(fonts):
    p = fonts.page
    picker(p)
    p.evaluate("() => {window.queryLocalFonts = async () => {throw new DOMException('denied','NotAllowedError')};}")
    p.locator("#list-device-fonts").click()
    expect(p.locator("#font-message")).to_contain_text("파일")
    p.locator("#font-files").set_input_files(files(b"slow"))
    p.wait_for_function("typeof finishInspect === 'function'")
    p.locator("#close-fonts").click()
    p.locator("#file").set_input_files({"name": "new.md", "mimeType": "text/plain", "buffer": b"new"})
    expect(fonts.source).to_have_value("new")
    p.evaluate("finishInspect()")
    picker(p)
    expect(p.locator("#font-family option", has_text="Paper")).to_have_count(0)
    assert len(fonts.requests) == 1


def test_style_only_missing_pair_relinks_by_digest_and_archives_only_used_faces(fonts):
    p = fonts.page
    p.route("**/api/style/open", lambda route: route.fulfill(json={
        **p.evaluate("styleForm.get()"), "body_font": FAMILY,
        "text_styles": {"note": {"font": FAMILY}}}))
    open_settings(p)
    p.locator("#style-file").set_input_files({"name": "foreign.json", "mimeType": "application/json", "buffer": b"{}"})
    expect(p.locator("#body_font")).to_have_value(FAMILY)
    assert p.evaluate("styleForm.get().text_styles.note.font") == FAMILY
    p.locator("#close-settings").click()
    choose_pair(p)
    p.route("**/api/style/export", lambda route: route.fulfill(json=route.request.post_data_json))
    p.evaluate("""() => { JjokArchive.pack = async ({fonts, style}) => new Blob([JSON.stringify({
        family:style.body_font, fonts:await Promise.all(fonts.map(async f =>
          [f.hash, await f.file.text(), await f.notice.text()]))})]); }""")
    p.locator("#include-proof").uncheck()
    with p.expect_download() as saved:
        p.locator("#save-document").click()
    assert json.loads(saved.value.path().read_bytes()) == {
        "family": FAMILY, "fonts": [[REG, "regular", "OFL notice"], [B, "bold", "OFL notice"]]}


def test_native_device_selection_reads_only_requested_file_and_frames_rule_and_model(browser):
    import base64
    from test_editor import ROOT

    data = (ROOT / "tests/fixtures/fonts/abel/Abel-Regular.ttf").read_bytes()
    digest = "8809dcad25318225052f88333e208c5aad4adcb7b2c934c135735ec19aa410b4"
    with browser.new_context() as context:
        page = context.new_page()
        page.set_default_timeout(2500)
        page.add_init_script("""
        window.__TAURI__ = {};
        window.fontReads = [];
        window.nativeFrames = [];
        const pdf = new Blob(["%PDF"], {type:"application/pdf"});
        window.desktop = {
          proof: async () => pdf, setDirty: async () => {}, setStyleDirty: async () => {},
          pages: async () => Object.assign(document.createElement("div"), {id:"desktop-pages"}),
          listFonts: async () => [{id:"unused", name:"Other"}, {id:"abel", name:"Abel Regular"},
            {id:"collection", name:"Collection", reason:"단일 파일만 지원합니다."}],
          readFont: async id => {fontReads.push(id); return new Blob([Uint8Array.from(atob("FONT_BASE64"), c => c.charCodeAt(0))]);},
          style: async () => ({body_sp:720896}),
          fontProof: async bytes => {
            const end = new DataView(bytes.buffer).getUint32(0) + 4;
            const header = JSON.parse(new TextDecoder().decode(bytes.subarray(4, end)));
            const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes.subarray(end))),
              n => n.toString(16).padStart(2,"0")).join("");
            nativeFrames.push({header, hash});
            return {rule:pdf};
          }
        };""".replace("FONT_BASE64", base64.b64encode(data).decode()))
        desk = Desk(page)
        expect(page.locator("#status")).to_contain_text("최신")
        picker(page)
        page.locator("#list-device-fonts").click()
        blocked = page.locator("#device-fonts option", has_text="Collection")
        expect(blocked).to_be_disabled()
        expect(blocked).to_contain_text("단일 파일")
        page.locator("#device-fonts").select_option(label="Abel Regular")
        assert page.evaluate("fontReads") == []
        page.locator("#use-device-fonts").click()
        expect(page.locator("#font-family")).to_have_value(f"font-{digest}-none")
        expect(page.locator("#font-evidence")).to_contain_text("허용")
        page.locator("#apply-font").click()
        expect(page.locator("#font-fallback")).to_contain_text("굵게")
        for provider in ["rule", "builtin"]:
            if provider == "builtin":
                page.evaluate("applyConnection({provider:'builtin'})")
            desk.refresh.click()
            page.wait_for_function("n => nativeFrames.length === n", arg=1 if provider == "rule" else 2)
            result = page.evaluate("nativeFrames.at(-1)")
            header = result["header"]
            assert result["hash"] == digest
            assert header["faces"] == [{"hash": digest, "size": len(data)}]
            assert header["request"] == {"source": desk.source.input_value(), "style": {"body_sp": 720896},
                                         "connection": {"provider": provider}}
        assert page.evaluate("fontReads") == ["abel"] and not desk.requests


def test_device_batch_keeps_readable_faces_and_groups_same_version(fonts):
    page = fonts.page
    page.evaluate("""() => {
      const inspect = FontRights.inspect;
      FontRights.inspect = async (blob, known) => {
        const info = await inspect(blob, known);
        const text = await blob.text();
        return {...info, family:"Paper", name:"Paper " + text,
                version:text.endsWith("2") ? "2" : "1", weight:text.startsWith("b") ? "bold" : "regular"};
      };
      window.queryLocalFonts = async () => ["r1","unreadable","r2","b1","b2"].map(name => ({
        fullName:name, postscriptName:name, blob:async () => {
          if (name === "unreadable") throw Error("파일 읽기 실패");
          return new Blob([name]);
        }}));
    }""")
    picker(page)
    page.locator("#list-device-fonts").click()
    page.locator("#device-fonts").select_option(index=[0, 1, 2, 3, 4])
    page.locator("#use-device-fonts").click()
    expect(page.locator("#font-message")).to_contain_text("unreadable")
    expected = []
    for v in ["1", "2"]:
        reg, bold = (hashlib.sha256((w + v).encode()).hexdigest() for w in ["r", "b"])
        expected.append(f"font-{reg}-{bold}")
    options = page.locator('#font-family option[value^="font-"]')
    expect(options).to_have_count(2)
    assert set(options.evaluate_all("opts => opts.map(option => option.value)")) == set(expected)
    assert len(set(options.all_text_contents())) == 2


def test_missing_archived_regular_marks_saved_pdf_stale_and_preserves_reference(fonts):
    page = fonts.page
    page.evaluate("""family => {
      JjokArchive.unpack = async () => ({source:"Stored manuscript", style:{...styleForm.get(),body_font:family},
        includeProof:true, proof:{mode:"model", state:"current",blob:new Blob(["saved proof"],{type:"application/pdf"})},
        fonts:[{hash:family.split("-")[1],weight:"regular",name:"Old Paper",version:"1"}]});
    }""", f"font-{REG}-none")
    page.route("**/api/style/open", lambda route: route.fulfill(json=route.request.post_data_json))
    page.locator("#document-file").set_input_files({"name":"paper.jjok","mimeType":"application/zip","buffer":b"data"})
    expect(fonts.source).to_have_value("Stored manuscript")
    expect(page.locator("#status")).to_contain_text("수정 전 교정본")
    expect(page.locator("#body_font")).to_have_value(f"font-{REG}-none")
    expect(page.locator("#font-fallback")).to_contain_text("Old Paper")
    assert len(fonts.requests) == 1
