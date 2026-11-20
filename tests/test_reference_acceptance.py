"""Host acceptance against an operator-selected local reference service and model."""

import hashlib
import json
import os
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader

from proof_response import selected_pdf
from pdf_metrics import read_pages
from test_article_acceptance import save
from test_list_acceptance import download_proof, match_item, rows
from test_refactor_acceptance import compact_json, image_id, stock


PROOF = Path(__file__).resolve().parents[1] / "proofs/reference-style"
BASELINE = "4ea8b4df9b6b57034134416c37bead5099976278f5580386cf5980fcb0418f8c"
PATH = ["들판 기록", "모래톱"]


@pytest.fixture(scope="module")
def url():
    base = os.environ.get("JJOKGYEOL_REFERENCE_URL", "").rstrip("/")
    if not base:
        pytest.skip("set JJOKGYEOL_REFERENCE_URL to run operator-connected browser acceptance")
    assert urlsplit(base).hostname in {"localhost", "127.0.0.1", "::1"}
    return base


@pytest.fixture
def editor(url):
    with sync_playwright() as p, p.chromium.launch() as browser:
        page = browser.new_page()
        page.goto(url)
        expect(page.locator("#status")).to_have_text("최신 버전", timeout=45000)
        yield page


def test_operator_model_and_structural_targets(url):
    with httpx.Client(base_url=url, trust_env=False) as client:
        settings = client.get("/api/reference/settings")
        assert settings.status_code == 200, settings.text
        assert settings.json()["max_bytes"] > 0
        assert settings.json()["model"]
        source = "# 숲\n\n## 동쪽\n\n## 동쪽\n\n# 바다\n\n## 동쪽"
        result = client.post("/api/reference/targets", json={"source": source})
        result.raise_for_status()
        counts = {tuple(row["path"]): row["count"] for row in result.json()["targets"]}
        assert counts["숲", "동쪽"] == 2
        assert counts["바다", "동쪽"] == 1


def test_reference_import_visible_in_real_editor(editor):
    control = editor.get_by_text("참고 문서 서식 가져오기", exact=True)
    assert control.count() > 0, "참고 문서 서식 가져오기 must be reachable in the real editor"


def upload(page, name, *, directory=PROOF):
    # Chromium can evict File-upload responses from its DevTools cache. Observe
    # the JSON the browser reads without replacing the HTTP request or its reply.
    page.evaluate("""() => {
        if (!window.referenceReplies) {
            window.referenceReplies = [];
            const json = Response.prototype.json;
            Response.prototype.json = async function(...args) {
                const value = await json.apply(this, args);
                if (new URL(this.url).pathname === "/api/reference/analyze")
                    window.referenceReplies.push(structuredClone(value));
                return value;
            };
        }
        window.referenceReplies.length = 0;
    }""")
    page.locator("#open-reference").click()
    expect(page.locator("#reference-file")).to_be_attached()
    path = directory / name
    page.locator("#reference-file").set_input_files(path)
    with page.expect_response(lambda r: "/api/reference/analyze?" in r.url, timeout=210000) as incoming:
        page.locator("#reference-analyze").click()
    response = incoming.value
    assert response.ok, response.text()
    assert int(response.request.header_value("content-length")) == path.stat().st_size
    page.wait_for_function("window.referenceReplies.length === 1", timeout=10000)
    result = page.evaluate("window.referenceReplies[0]")
    assert isinstance(result["warnings"], list) and all(isinstance(w, str) for w in result["warnings"])
    expect(page.locator("#reference-analyze")).to_be_enabled(timeout=10000)
    for unit in result["units"]:
        assert all(isinstance(unit[k], str) for k in ("location", "section", "excerpt"))
        for c in unit["candidates"]:
            assert c["basis"] in {"read", "estimated", "confirm", "unsupported"} and c["evidence"]
            if c["role"] in {"header", "footer"}:
                assert isinstance(c["source_group"], str) and c["source_group"]
                assert c["variant"] in {"default", "first", "even", "odd", "unknown"}
    print(name, [(u["location"], u["section"], len(u["candidates"])) for u in result["units"]], result["warnings"])
    return result


def only_unit(page, index):
    page.locator("#reference-start").select_option(str(index))
    page.locator("#reference-end").select_option(str(index))


def candidate(page, role, key):
    return page.locator(f'#reference-candidates [data-role="{role}"][data-key="{key}"]').first


@pytest.mark.parametrize("name", ["reference.pdf", "scan.png", "mixed.pdf"])
def test_pdf_scan_and_mixed_real_analysis(editor, name, tmp_path):
    if name != "reference.pdf":
        source, before = target(editor, tmp_path)
    result = upload(editor, name)
    assert not result["warnings"], result["warnings"]
    units = result["units"]
    assert len(units) == {"reference.pdf": 3, "scan.png": 1, "mixed.pdf": 2}[name]
    if name != "scan.png":
        body = units[2 if name == "reference.pdf" else 0]
        assert "잔별수첩" in body["excerpt"] or any("잔별수첩" in c["evidence"] for c in body["candidates"])
        fields = {(c["role"], c["key"]): c for c in body["candidates"]}
        assert fields["document", "page_mm"]["value"] == pytest.approx([148.167, 209.889], abs=.05)
        size = fields["body", "font_pt"]
        assert size["basis"] == "read" and float(size["value"]) == pytest.approx(11.036, abs=.1)
        # PDFMiner gives the period's right edge as 69.451022 PDF pt and
        # the next Korean glyph's origin as 72.749997, on both items.
        assert fields["list", "levels.1.gap_mm"]["value"] == pytest.approx(1.164, abs=.03)
    if name == "reference.pdf":
        assert [u["section"] for u in units] == ["표지", "목차", "본문"]
        return
    operator_model = editor.request.get(editor.url.rstrip("/") + "/api/reference/settings").json()["model"]
    assert operator_model
    expect(editor.locator("#reference-model")).to_contain_text(operator_model)
    if name == "mixed.pdf":
        only_unit(editor, 1)
        with editor.expect_response(lambda r: "/api/reference/analyze?" in r.url, timeout=210000) as reply:
            editor.get_by_role("button", name="2쪽 다시 분석").click()
        assert reply.value.ok and "page=2" in reply.value.url
        editor.wait_for_function("window.referenceReplies.length === 2", timeout=10000)
        retried = editor.evaluate("window.referenceReplies[1]")
        assert len(retried["units"]) == 1 and not retried["warnings"]
        assert retried["units"][0]["location"] == units[1]["location"]
        units[1] = retried["units"][0]
        only_unit(editor, 0)
        expect(candidate(editor, "body", "font_pt").get_by_label("제안 값")).to_have_value(str(size["value"]))
    scan = units[-1]
    assert scan.get("preview", "").startswith("data:image/")
    ratios = [c for c in scan["candidates"] if c.get("unit") == "width_per_10000"]
    assert ratios, (result["warnings"], scan)
    assert all(c["basis"] in ("estimated", "confirm") for c in ratios)
    only_unit(editor, len(units) - 1)
    measure, = [c for c in ratios if c["role"] == "body" and c["key"] == "font_pt"]
    row = candidate(editor, measure["role"], measure["key"])
    value = row.get_by_label("제안 값", exact=True)
    raw = measure["value"]
    raw = raw if isinstance(raw, list) else [raw]
    factor = 72.27 / 25.4 if measure["key"].endswith("_pt") else 1
    if name == "scan.png":
        expect(value).to_have_value("")
    else:
        page_width = next(c["value"][0] for c in scan["candidates"] if c["key"] == "page_mm")
        assert [float(n) for n in value.input_value().split(",")] == pytest.approx(
            [n * page_width * factor / 10000 for n in raw], abs=.001)
    editor.locator("#reference-width").fill("148")
    measured = [float(n) for n in value.input_value().split(",")]
    assert measured == pytest.approx([n * 148 * factor / 10000 for n in raw], abs=.001)
    print("LM calibration", measure["role"], measure["key"], raw, measured)
    # A clean raster of the independently measured 11 pt body. Allow the ink
    # height to differ from an em box, but catch order-of-magnitude errors.
    assert measured[0] == pytest.approx(11, abs=2.2)
    choose(editor, "body", "font_pt")
    after = commit_pdf(editor, trial(editor), tmp_path / "scan-applied.pdf")
    assert body_sample(after, "모래시계")[2].size == pytest.approx(measured[0] * 72 / 72.27, abs=.002)
    assert body_sample(before, "갈대이삭")[2].size == pytest.approx(body_sample(after, "갈대이삭")[2].size, abs=.001)
    expect(editor.locator("#source")).to_have_value(source)


def geometry(pdf):
    return hashlib.sha256(compact_json([asdict(p) for p in read_pages(pdf)])).hexdigest()


def target(page, folder):
    source = (PROOF / "target.md").read_text(encoding="utf-8")
    # BASELINE was printed with the original fixed-spacing publication settings.
    original = json.loads((PROOF / "style.json").read_bytes())
    original.pop("section_rules")
    with page.expect_response(lambda reply: reply.url.endswith("/api/style/open")) as opened:
        page.locator("#style-file").set_input_files({
            "name": "기존 양식.json", "mimeType": "application/json", "buffer": compact_json(original)})
    assert opened.value.ok
    page.locator("#file").set_input_files(PROOF / "target.md")
    expect(page.locator("#source")).to_have_value(source)
    before = download_proof(page, folder / "before.pdf")
    assert geometry(before) == BASELINE
    return source, before


def choose(page, role, key, value=None):
    row = candidate(page, role, key)
    row.get_by_role("checkbox").check()
    row.get_by_label("적용 대상", exact=True).select_option(json.dumps(PATH, ensure_ascii=False, separators=(",", ":")))
    if value is not None:
        row.get_by_label("제안 값", exact=True).fill(value)
    return row


def blob_hash(locator, attribute):
    return locator.evaluate("""async (el, name) => {
        const bytes = await (await fetch(el.getAttribute(name))).arrayBuffer();
        const hash = await crypto.subtle.digest('SHA-256', bytes);
        return Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, '0')).join('');
    }""", attribute)


def trial(page):
    with page.expect_response(lambda r: r.url.endswith("/api/render"), timeout=45000) as incoming:
        page.locator("#reference-trial").click()
    assert incoming.value.ok, incoming.value.text()
    raw = selected_pdf(incoming.value, page)
    expect(page.locator("#reference-apply")).to_be_enabled(timeout=45000)
    assert blob_hash(page.locator("#reference-proof a"), "href") == hashlib.sha256(raw).hexdigest()
    return raw


def commit_pdf(page, trial_pdf, path):
    page.locator("#reference-apply").click()
    expect(page.locator("#reference-review")).to_be_hidden()
    assert blob_hash(page.locator("#preview"), "src") == hashlib.sha256(trial_pdf).hexdigest()
    pdf = save(page, "#export")
    assert pdf == trial_pdf
    path.write_bytes(pdf)
    return pdf


def body_sample(pdf, word):
    lines = rows(pdf)
    i, label, first = match_item(lines, word)
    return lines[i][0], "".join(c.text for c in label), first


@pytest.mark.parametrize("name", ["reference.docx", "reference.hwpx"])
def test_office_body_only_trial_rollback_and_roundtrip(editor, tmp_path, name):
    source, before = target(editor, tmp_path)
    committed = hashlib.sha256(before).hexdigest()
    editor.locator("#style summary").click()
    old_style = save(editor, "#save-style")

    for action in ("cancel", "apply"):
        units = upload(editor, name)["units"]
        for marker, role, size in (("별자리관측소", "heading", 24), ("관측 거리", "body", 13),
                                   ("잔별수첩", "body", 11)):
            i, = [i for i, u in enumerate(units) if marker in u["excerpt"]]
            assert any(c["role"] == role and c["key"] == "font_pt" and c["value"] == size and c["basis"] == "read"
                       for c in units[i]["candidates"])
        only_unit(editor, i)
        choose(editor, "body", "font_pt", "14")
        proposed = trial(editor)
        assert blob_hash(editor.locator("#preview"), "src") == committed
        if action == "cancel":
            editor.locator("#reference-cancel").click()
            assert save(editor, "#export") == before
            assert save(editor, "#save-style") == old_style
            expect(editor.locator("#source")).to_have_value(source)
    after = commit_pdf(editor, proposed, tmp_path / "after.pdf")
    expect(editor.locator("#source")).to_have_value(source)
    style = save(editor, "#save-style")
    (tmp_path / "style.json").write_bytes(style)
    stored = json.loads(style)
    assert stored.pop("section_rules") == [{"path": PATH, "role": "body", "values": {"font_sp": 917504}}]
    assert stored == json.loads(old_style)
    for word in ("들판기록", "모래톱", "모래시계", "물수제비", "갈대이삭", "억새그늘"):
        _, old_marker, left = body_sample(before, word)
        _, new_marker, right = body_sample(after, word)
        assert new_marker == old_marker and right.font == left.font
        assert right.x0 == pytest.approx(left.x0, abs=.001)
        assert right.size == pytest.approx(14 * 72 / 72.27 if word == "모래시계" else left.size, abs=.001)
    visible = "".join(c.text for _, line in rows(after) for c in line)
    for secret in ("별자리관측소", "잔별수첩", "망원경"):
        assert secret not in visible and secret not in style.decode()
    assert not any(len(p.images) for p in PdfReader(BytesIO(after)).pages)
    # Load a legacy style first so an ignored reopen cannot pass by coincidence.
    def reopen(raw):
        with editor.expect_response(lambda r: r.url.endswith("/api/style/open")) as response:
            editor.locator("#style-file").set_input_files({"name": "양식.json", "mimeType": "application/json", "buffer": raw})
        assert response.value.ok
        expect(editor.locator("#save-style")).to_be_enabled()
    reopen(old_style)
    assert geometry(download_proof(editor, tmp_path / "legacy.pdf")) == BASELINE
    reopen(style)
    assert geometry(download_proof(editor, tmp_path / "reopened.pdf")) == geometry(after)
    print(name, "body glyph", body_sample(before, "모래시계")[2].size, body_sample(after, "모래시계")[2].size,
          "geometry", geometry(after), "trial/preview/download", hashlib.sha256(after).hexdigest())


def test_list_scoped_indent_follows_moved_section_and_rebinds(editor, tmp_path):
    source, before = target(editor, tmp_path)
    units = upload(editor, "reference.docx")["units"]
    index, = [i for i, u in enumerate(units) if "망원경" in u["excerpt"]]
    only_unit(editor, index)
    row = choose(editor, "list", "levels.1.indent_mm")
    amount = row.get_by_label("제안 값", exact=True)
    assert float(amount.input_value()) == pytest.approx(6.35)
    amount.fill("-1")
    editor.locator("#reference-trial").click()
    expect(editor.locator("#reference-message")).not_to_be_empty(timeout=10000)
    expect(editor.locator("#reference-apply")).to_be_disabled()
    assert blob_hash(editor.locator("#preview"), "src") == hashlib.sha256(before).hexdigest()
    amount.fill("6.35")
    after = commit_pdf(editor, trial(editor), tmp_path / "list.pdf")

    def check(pdf, shifted):
        for word in ("물수제비", "자갈무늬", "억새그늘", "모래시계"):
            _, old_marker, left = body_sample(before, word)
            _, new_marker, right = body_sample(pdf, word)
            assert old_marker == new_marker and left.size == pytest.approx(right.size, abs=.001)
            step = 18 if shifted and word in ("물수제비", "자갈무늬") else 0
            assert right.x0 - left.x0 == pytest.approx(step, abs=.025)
    check(after, True)
    prefixed = "# 머리글\n\n" + "이른 새벽 물결을 살피고 발자국을 기록한다.\n\n" * 30 + source
    editor.locator("#source").fill(prefixed)
    moved = download_proof(editor, tmp_path / "moved.pdf")
    assert body_sample(moved, "물수제비")[0] > body_sample(after, "물수제비")[0]
    check(moved, True)
    editor.locator("#source").fill(source + "\n\n## 모래톱\n\n별도 구역")
    check(download_proof(editor, tmp_path / "duplicate.pdf"), False)
    renamed = source.replace("## 모래톱", "## 물안개")
    editor.locator("#source").fill(renamed)
    check(download_proof(editor, tmp_path / "missing.pdf"), False)
    editor.locator("#open-reference").click()
    binding = editor.locator("#reference-bindings").get_by_label("다시 연결할 대상")
    binding.select_option(label="들판 기록 › 물안개")
    rebound = commit_pdf(editor, trial(editor), tmp_path / "rebound.pdf")
    check(rebound, True)
    expect(editor.locator("#source")).to_have_value(renamed)
    print("list x", body_sample(before, "물수제비")[2].x0, body_sample(rebound, "물수제비")[2].x0,
          "pages", body_sample(after, "물수제비")[0], body_sample(moved, "물수제비")[0])


def test_mixed_partial_success_without_operator_connection(url, request):
    offline = os.environ.get("JJOKGYEOL_REFERENCE_OFFLINE_URL")
    if offline:
        assert urlsplit(offline).hostname in {"localhost", "127.0.0.1", "::1"}
    else:
        _, offline = request.getfixturevalue("stock")
    settings = httpx.get(offline + "/api/reference/settings", trust_env=False)
    settings.raise_for_status()
    assert not settings.json()["model"]
    with sync_playwright() as p, p.chromium.launch() as b:
        page = b.new_page()
        page.goto(offline)
        result = upload(page, "mixed.pdf")
        first, second = result["units"]
        assert "잔별수첩" in first["excerpt"]
        assert any(c["key"] == "font_pt" and c["basis"] == "read" for c in first["candidates"])
        assert result["warnings"] and not any(c["unit"] == "width_per_10000" for c in second["candidates"] if "unit" in c)
        with page.expect_response(lambda r: "/api/reference/analyze?" in r.url) as retried:
            page.get_by_role("button", name="2쪽 다시 분석").click()
        assert "page=2" in retried.value.url and retried.value.ok
        page.wait_for_function("window.referenceReplies.length === 2")
        assert page.evaluate("window.referenceReplies[1].units.length") == 1
        only_unit(page, 0)
        expect(candidate(page, "body", "font_pt").get_by_role("checkbox")).to_be_enabled()
