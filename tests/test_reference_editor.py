"""Reference review in Chromium, with a controlled analyzer and render completion order."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from settings_ui import open_settings, close_settings

from app import style
from test_editor import Desk, ORIGIN, STATIC, browser
from test_slice import server_url


def candidate(role, key, value, basis="read", **extra):
    return dict(role=role, key=key, value=value, basis=basis, evidence="문단 3의 직접 지정", **extra)


def unit(page, section, *candidates):
    return dict(page=page, section=section, location=f"{page}쪽", excerpt="참고 전용 문장",
                candidates=list(candidates))


UNITS = [
    unit(1, "표지", candidate("heading", "font_pt", 24),
         candidate("header", "left", "기관명", source_group="section-1-header", variant="default")),
    unit(2, "본문", candidate("body", "font_pt", "11"), candidate("body", "font", "알 수 없는 글꼴", "confirm")),
    unit(3, "본문", candidate("body", "font_pt", "12"), candidate("list", "levels.1.gap_mm", 100, "estimated", unit="width_per_10000")),
]


class Review(Desk):
    def __init__(self, page, service):
        self.service = service
        self.analyzed = []
        self.targets = [{"path": ["서문"], "count": 1}, {"path": ["본문"], "count": 1},
                        {"path": ["중복"], "count": 2}]
        super().__init__(page)

    def route(self, route):
        url = urlsplit(route.request.url)
        if url.path.startswith("/static/reference") and (STATIC / url.path.split("/")[-1]).is_file():
            route.fulfill(path=STATIC / url.path.split("/")[-1])
        elif url.path == "/api/reference/settings":
            route.fulfill(json={"model": "검증 모델", "max_bytes": 2048})
        elif url.path == "/api/reference/targets":
            route.fulfill(json={"targets": self.targets})
        elif url.path == "/api/reference/analyze":
            self.analyzed.append((parse_qs(url.query), route.request.post_data_buffer))
            units = UNITS if "page" not in self.analyzed[-1][0] else [unit(3, "본문", candidate("list", "levels.1.gap_mm", 3))]
            route.fulfill(json={"units": units, "warnings": ["3쪽 추정 확인"]})
        elif url.path.startswith("/api/style/"):
            route.fulfill(response=route.fetch(url=self.service + url.path))
        else:
            super().route(route)

    def open(self):
        open_settings(self.page)
        self.page.get_by_role("button", name="참고 문서 서식 가져오기").click(timeout=1200)
        expect(self.page.locator("#reference-model")).to_contain_text("검증 모델")

    def analyze(self):
        self.page.locator("#reference-file").set_input_files({
            "name": "참고 #1.pdf", "mimeType": "application/pdf", "buffer": b"%PDF reference fixture"})
        self.page.locator("#reference-analyze").click()
        expect(self.page.locator("#reference-candidates")).to_contain_text("참고 전용 문장")
        self.page.locator("#reference-end").select_option("2")

    def row(self, role, key, index=0):
        return self.page.locator(f'#reference-candidates [data-role="{role}"][data-key="{key}"]').nth(index)

    def choose(self, role="body", key="font_pt", index=0, value="14"):
        row = self.row(role, key, index)
        row.get_by_role("checkbox").check()
        if value is not None:
            row.get_by_label("제안 값", exact=True).fill(value)
        row.get_by_label("적용 대상", exact=True).select_option(label="본문")
        return row


@pytest.fixture
def review(browser, server_url):
    with browser.new_context() as context:
        page = context.new_page()
        page.set_default_timeout(3000)
        desk = Review(page, server_url)
        desk.ready()
        yield desk


def test_selected_style_trial_cancel_commit_and_reopen(review):
    d = review
    p = d.page
    d.open()
    d.analyze()
    assert d.analyzed == [({"name": ["참고 #1.pdf"]}, b"%PDF reference fixture")]
    expect(d.row("header", "left").get_by_role("checkbox")).to_be_enabled()
    d.choose()
    previous = d.frame.get_attribute("src")
    trial = p.locator("#reference-trial")
    d.submit(trial)
    style = d.requests[-1]["style"]
    assert style["section_rules"] == [{"path": ["본문"], "role": "body", "values": {"font_pt": "14"}}]
    assert d.requests[-1]["source"] == d.source.input_value() and "참고 전용" not in str(d.requests[-1])
    d.reply(0, d.pdf)
    expect(p.locator("#reference-apply")).to_be_enabled()
    assert p.locator("#preview").get_attribute("src") == previous
    p.locator("#reference-cancel").click()
    assert p.locator("#preview").get_attribute("src") == previous
    d.open()
    d.analyze()
    d.choose()
    d.submit(trial)
    d.reply(1, d.pdf + b"\n% verified\n")
    p.locator("#reference-apply").click()
    close_settings(p)
    with p.expect_download() as pdf:
        d.export.click()
    assert pdf.value.url == p.locator("#preview").get_attribute("src")
    assert pdf.value.path().read_bytes() == d.pdf + b"\n% verified\n"
    open_settings(p)
    p.locator("#style summary").click()
    with p.expect_download() as saved:
        p.locator("#save-style").click()
    stored = saved.value.path().read_bytes()
    assert "참고 전용" not in stored.decode() and json.loads(stored)["section_rules"]
    open_settings(p)
    p.locator("#style-file").set_input_files({"name": "서식.json", "mimeType": "application/json", "buffer": stored})
    expect(p.locator("#save-style")).to_be_enabled()
    d.targets = [{"path": ["새 제목"], "count": 1}]
    close_settings(p)
    d.source.fill("# 새 제목\n\n원고")
    d.open()
    binding = p.locator("#reference-bindings").get_by_role("combobox")
    expect(binding).to_contain_text("새 제목")
    binding.select_option(label="새 제목")
    d.submit(trial)
    assert d.requests[-1]["style"]["section_rules"] == [{"path": ["새 제목"], "role": "body", "values": {"font_pt": "14"}}]
    d.reply(2, d.pdf)


def test_conflicts_relative_width_fonts_and_partial_retry(review):
    d = review
    p = d.page
    d.open()
    d.analyze()
    d.choose(value="14")
    second = d.choose(index=1, value="15")
    trial = p.locator("#reference-trial")
    trial.click()
    expect(p.locator("#reference-message")).to_contain_text("충돌")
    second.get_by_role("checkbox").uncheck()
    font = d.choose(key="font", value=None)
    trial.click()
    expect(p.locator("#reference-message")).to_contain_text("글꼴")
    font.get_by_label("제안 값", exact=True).select_option("nanum-gothic")
    gap = d.choose("list", "levels.1.gap_mm", value=None)
    trial.click()
    expect(p.locator("#reference-message")).to_contain_text("기준 폭")
    p.locator("#reference-width").fill("200")
    expect(gap.get_by_label("제안 값", exact=True)).to_have_value("2")
    d.submit(trial)
    assert d.requests[-1]["style"]["section_rules"] == [
        {"path": ["본문"], "role": "body", "values": {"font_pt": "14", "font": "nanum-gothic"}},
        {"path": ["본문"], "role": "list", "values": {"levels.1.gap_mm": "2"}}]
    d.reply(0, {"field": "section_rules", "message": "시험 값 오류"}, 422)
    expect(p.locator("#reference-message")).to_contain_text("시험 값 오류")
    expect(p.locator("#reference-apply")).to_be_disabled()
    p.get_by_role("button", name="3쪽 다시 분석").click()
    expect(d.row("list", "levels.1.gap_mm").get_by_label("제안 값", exact=True)).to_have_value("3")
    expect(d.row("body", "font_pt").first.get_by_label("제안 값", exact=True)).to_have_value("14")
    assert d.analyzed[-1][0]["page"] == ["3"]
    p.locator("#reference-end").select_option("0")
    expect(d.row("body", "font_pt")).to_have_count(0)


def test_late_trial_after_edit_cannot_be_applied(review):
    d = review
    p = d.page
    d.open()
    d.analyze()
    d.choose()
    # Model a response already in transit when the browser requests cancellation.
    p.evaluate("""() => {
      const fetch = window.fetch;
      window.fetch = (url, options) => fetch(url,
        url === '/api/render' ? {...options, signal: undefined} : options);
    }""")
    previous = p.locator("#preview").get_attribute("src")
    d.submit(p.locator("#reference-trial"))
    close_settings(p)
    d.source.fill("# 새 원고")
    open_settings(p)
    d.reply(0, d.pdf)
    expect(p.locator("#reference-apply")).to_be_disabled()
    assert p.locator("#preview").get_attribute("src") == previous


def test_scan_page_width_and_explicit_overrides(review):
    d = review
    p = d.page
    units = [unit(1, "스캔", candidate("document", "page_mm", [254, 300]),
                  candidate("body", "font_pt", 100, "estimated", unit="width_per_10000"),
                  candidate("document", "margins_mm", [100, 200, 300, 400], "estimated", unit="width_per_10000"))]
    p.route("**/api/reference/analyze?*", lambda r: r.fulfill(json={"units": units, "warnings": []}))
    d.open()
    p.locator("#reference-file").set_input_files({"name": "인쇄.pdf", "mimeType": "application/pdf", "buffer": b"test"})
    p.locator("#reference-analyze").click()
    width = p.locator("#reference-width")
    expect(width).to_have_value("254")
    size = d.row("body", "font_pt").get_by_label("제안 값", exact=True)
    expect(size).to_have_value("7.227")
    margins = d.row("document", "margins_mm").get_by_label("제안 값", exact=True)
    expect(margins).to_have_value("2.54, 5.08, 7.62, 10.16")
    size.fill("14")
    width.fill("200")
    expect(size).to_have_value("14")
    expect(margins).to_have_value("2, 4, 6, 8")


def test_office_excerpt_shows_representative_format(review):
    review.open()
    review.analyze()
    excerpt = review.page.locator(".reference-unit").first.locator(".reference-excerpt")
    expect(excerpt).to_have_text("참고 전용 문장")
    assert excerpt.evaluate("el => getComputedStyle(el).fontSize") == "32px"  # 24 CSS pt


def test_current_values_follow_heading_level_override_and_list_origin(review):
    d, p = review, review.page
    d.targets = [{"path": ["본문"], "count": 1, "level": 3}, {"path": ["서문"], "count": 1, "level": 1}]
    p.route("**/api/reference/settings", lambda r: r.fulfill(json={
        "model": "검증 모델", "max_bytes": 4096,
        "heading_per_10000": [17000, 14500, 12500, 11500, 10500, 10000],
        "heading_leading_per_10000": 13000,
    }))
    result = [unit(1, "서식", candidate("heading", "font_pt", 14), candidate("heading", "leading_pt", 18),
                   candidate("list", "first_indent_mm", 2), candidate("list", "rest_indent_mm", 3),
                   candidate("body", "color", "#aacc11"))]
    p.route("**/api/reference/analyze?*", lambda r: r.fulfill(json={"units": result, "warnings": []}))
    file = p.request.post(d.service + "/api/style/export", data={
        "first_indent_mm": "4", "rest_indent_mm": "6",
        "section_rules": [{"path": ["서문"], "role": "heading", "values": {"font_pt": "14"}}],
    })
    assert file.ok, file.text()
    open_settings(p)
    p.locator("#style-file").set_input_files({"name": "양식.json", "mimeType": "application/json", "buffer": file.body()})
    p.locator("#style summary").click()
    expect(p.locator("#first_indent_mm")).to_have_value("4")
    d.open()
    p.locator("#reference-file").set_input_files({"name": "참고.docx", "mimeType": "application/octet-stream", "buffer": b"test"})
    p.locator("#reference-analyze").click()
    for key, body, front in (("font_pt", "13.75", "14"), ("leading_pt", "17.875", "18.2")):
        row = d.row("heading", key)
        expect(row).to_contain_text("현재 값 · 제목마다 다름")
        target = row.get_by_label("적용 대상", exact=True)
        target.select_option(label="본문")
        expect(row).to_contain_text("현재 값 · " + body)
        target.select_option(label="서문")
        expect(row).to_contain_text("현재 값 · " + front)
    for key, body in (("first_indent_mm", "4"), ("rest_indent_mm", "6")):
        row = d.row("list", key)
        expect(row).to_contain_text("현재 값 · 0")
        row.get_by_label("적용 요소", exact=True).select_option("body")
        expect(row).to_contain_text("현재 값 · " + body)
    expect(d.row("body", "color")).to_contain_text("현재 값 · #000000")


def test_current_values_follow_list_levels_and_section_rule(review):
    d, p = review, review.page
    row = {"expr": "-", "indent_mm": 0, "gap_mm": 1, "font": "nanum-gothic"}
    file = p.request.post(d.service + "/api/style/export", data={
        "list_levels": [{**row, "font_pt": 16}, row],
        "section_rules": [{"path": ["서문"], "role": "list", "values": {"font_pt": "14"}}],
    })
    assert file.ok, file.text()
    open_settings(p)
    p.locator("#style-file").set_input_files({"name": "목록.json", "mimeType": "application/json", "buffer": file.body()})
    p.locator("#style summary").click()
    expect(p.locator("#list-preset")).to_have_value("custom")
    d.open()
    result = [unit(1, "서식", candidate("list", "font_pt", 15), candidate("list", "font", "nanum-myeongjo"))]
    p.route("**/api/reference/analyze?*", lambda r: r.fulfill(json={"units": result, "warnings": []}))
    p.locator("#reference-file").set_input_files({"name": "참고.docx", "mimeType": "application/octet-stream", "buffer": b"test"})
    p.locator("#reference-analyze").click()
    size = d.row("list", "font_pt")
    expect(size).to_contain_text("현재 값 · 단계마다 다름")
    expect(d.row("list", "font")).to_contain_text("현재 값 · 나눔고딕")
    size.get_by_label("적용 대상", exact=True).select_option(label="서문")
    expect(size).to_contain_text("현재 값 · 14")


def test_page_retry_keeps_reviewed_read_value_on_the_same_page(review):
    d, p = review, review.page
    page_size = candidate("document", "page_mm", [148, 210])
    seen = []
    def respond(route):
        retry = "page=1" in route.request.url
        seen.append(retry)
        candidates = [page_size, candidate("body", "color", "#112233")] if retry else [page_size]
        route.fulfill(json={"units": [unit(1, "본문", *candidates)], "warnings": []})
    p.route("**/api/reference/analyze?*", respond)
    d.open()
    p.locator("#reference-file").set_input_files({"name": "부분.pdf", "mimeType": "application/pdf", "buffer": b"test"})
    p.locator("#reference-analyze").click()
    row = d.row("document", "page_mm")
    row.get_by_role("checkbox").check()
    row.get_by_label("제안 값", exact=True).fill("152, 220")
    p.get_by_role("button", name="1쪽 다시 분석").click()
    expect(d.row("body", "color")).to_be_visible()
    expect(row.get_by_role("checkbox")).to_be_checked()
    expect(row.get_by_label("제안 값", exact=True)).to_have_value("152, 220")
    d.submit(p.locator("#reference-trial"))
    assert d.requests[-1]["style"]["page_mm"] == ["152", "220"]
    assert seen == [False, True]
    d.reply(0, d.pdf)


def test_bootstrap_with_section_rules_starts_sample(browser, monkeypatch, server_url):
    settings = style.editor_bootstrap()
    defaults = {**settings["defaults"], "section_rules": [{"path": ["지난 원고"], "role": "body", "values": {"font_pt": "14"}}]}
    monkeypatch.setattr(style, "editor_bootstrap", lambda: {**settings, "defaults": defaults})
    with browser.new_context() as context:
        page = context.new_page()
        d = Review(page, server_url)
        d.ready({**defaults, **settings["new_spacing"], "text_styles": settings["new_text_styles"],
                 "paragraph_layout": settings["new_paragraph_layout"]})
        open_settings(page)
        expect(page.locator("#reference-notice")).to_contain_text("다시 연결")


def test_long_section_keeps_selections_across_visible_units(review):
    d, p = review, review.page
    units = [unit(i + 1, "본문", candidate("body", "font_pt", 12)) for i in range(30)]
    units[-1]["candidates"] = [candidate("list", "color", "#527219")]
    p.route("**/api/reference/analyze?*", lambda r: r.fulfill(json={"units": units, "warnings": []}))
    d.open()
    p.locator("#reference-file").set_input_files({"name": "보고서.pdf", "mimeType": "application/pdf", "buffer": b"test"})
    p.locator("#reference-analyze").click()
    d.choose()
    blocks = p.locator(".reference-unit")
    assert 0 < blocks.count() < len(units)
    following = p.get_by_role("button", name="다음 항목")
    while following.is_enabled():
        following.click()
    d.choose("list", "color", value=None)
    d.submit(p.locator("#reference-trial"))
    assert d.requests[-1]["style"]["section_rules"] == [
        {"path": ["본문"], "role": "body", "values": {"font_pt": "14"}},
        {"path": ["본문"], "role": "list", "values": {"color": "#527219"}}]
    d.reply(0, d.pdf)


@pytest.mark.parametrize("targets,expected", [
    ([{"path": ["하나"], "count": 1, "level": 1}], "18"),
    ([{"path": ["하나"], "count": 2, "level": 1, "levels": [1, 2]}], "제목마다 다름"),
])
def test_document_heading_current_values(review, targets, expected):
    d, p = review, review.page
    d.targets = targets
    row = {"font_pt": "18", "color": "#17365D", "font": "nanum-myeongjo"}
    file = p.request.post(d.service + "/api/style/export", data={
        "text_styles": {"heading1": row, "heading2": {**row, "font_pt": "14"}},
    })
    assert file.ok
    open_settings(p)
    p.locator("#style-file").set_input_files({"name": "제목.json", "mimeType": "application/json", "buffer": file.body()})
    expect(p.locator("#save-style")).to_be_enabled()
    d.open()
    p.route("**/api/reference/analyze?*", lambda r: r.fulfill(json={"units": [
        unit(1, "본문", *(candidate("heading", key, value) for key, value in row.items()))], "warnings": []}))
    p.locator("#reference-file").set_input_files({"name": "참고.docx", "mimeType": "application/octet-stream", "buffer": b"test"})
    p.locator("#reference-analyze").click()
    for key, value in [("font_pt", expected), ("color", "#17365D"), ("font", "나눔명조")]:
        expect(d.row("heading", key)).to_contain_text("현재 값 · " + value)
