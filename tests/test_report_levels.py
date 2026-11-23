"""Stored report levels, editor edits and the printed marker/body columns."""
import json

import pytest
from playwright.sync_api import expect

from app.document import InputError, parse_md
from settings_ui import open_settings
from app.style import Style, apply_style, editor_bootstrap, from_stored_style
from app.typeset import typeset
from test_editor import browser
from test_list_typeset import BP_PER_MM, baselines, text
from test_slice import server_url
from test_style_ai import model, reply


PT = 72 / 72.27
SP = 65536
ROWS = [
    {"expr": mark, "indent_sp": int(indent * SP), "gap_sp": 3 * SP, "indent_origin": "margin",
     "font_sp": size * SP, "font": font, "weight": weight}
    for mark, indent, size, font, weight in [
        ("□", 0, 16, "nanum-gothic", "bold"), ("○", 7.5, 15, "nanum-myeongjo", "normal"),
        ("-", 22.5, 15, "nanum-myeongjo", "normal"), ("*", 37.5, 13, "nanum-gothic", "normal"),
    ]
]
ROWS[2]["mark_weight"] = "bold"


def test_stored_editor_and_list_suggestion_round_trip():
    saved = {**Style().to_stored(), "list_levels": ROWS}
    style = from_stored_style(saved)
    editor = style.to_editor()
    assert [r["font_pt"] for r in editor["list_levels"]] == ["16", "15", "15", "13"]
    assert apply_style(Style(), editor).to_stored() == saved
    suggest = model(reply(("list.2.expr", "둘")))
    patch = suggest("둘째 표지 바꾸기", style, draft=editor)
    assert apply_style(style, patch).to_stored()["list_levels"] == [ROWS[0], {**ROWS[1], "expr": "둘"}, *ROWS[2:]]


@pytest.mark.parametrize(("key", "bad"), [
    ("font", "missing"), ("weight", "heavy"), ("mark_weight", None), ("indent_origin", "parent"),
    ("font_pt", 2048), ("font_pt", "0.000001"),
])
def test_invalid_editor_row_field(key, bad):
    with pytest.raises(InputError) as exc:
        apply_style(Style(), {"list_levels": [{"expr": "-", "indent_mm": 0, "gap_mm": 1, key: bad}]})
    assert exc.value.field == f"list_levels[0].{key}"


def test_stored_font_size_and_tracking_limits():
    for size in [0, 2048 * SP, 11.5, "983040"]:
        with pytest.raises(InputError) as exc:
            from_stored_style({**Style().to_stored(), "list_levels": [{**ROWS[0], "font_sp": size}]})
        assert exc.value.field == "list_levels[0].font_sp"
    row = {"expr": "-", "indent_mm": 0, "gap_mm": 1, "font_pt": "100"}
    valid = apply_style(Style(), {"list_levels": [row], "ko_tracking_bounds_pct": [0, 16383]})
    with pytest.raises(InputError):
        apply_style(valid, {"list_levels": [{**row, "font_pt": "100.00000000000000000001"}]})
    with pytest.raises(InputError) as exc:
        apply_style(valid, {"ko_tracking_bounds_pct": [0, 16384]})
    assert exc.value.field == "ko_tracking_bounds_pct"
    with pytest.raises(InputError) as exc:
        from_stored_style({**valid.to_stored(), "ko_tracking_bounds_per_10000": [0, 1638400]})
    assert exc.value.field == "ko_tracking_bounds_per_10000"
    with pytest.raises(InputError):
        apply_style(Style(), {"list_levels": [row], "ko_tracking_pct": 16384})


def face(char):
    return char.font.split("+")[-1]


def test_four_report_levels_and_continuations_in_pdf(tmp_path):
    source = ("- 하나\n  - 둘\n    - 셋\n      - 넷 " + "물결 " * 34 +
              "\n\n        이어진 문단\n\n본문")
    style = from_stored_style({**Style().to_stored(), "list_levels": ROWS})
    lines = [chars for _, chars in baselines(typeset(parse_md(source), style, tmp_path))]
    faces = ["NanumGothicBold", "NanumMyeongjo", "NanumMyeongjo", "NanumGothic"]
    for i, (mark, indent, size) in enumerate([("□", 0, 16), ("○", 7.5, 15), ("-", 22.5, 15), ("*", 37.5, 13)]):
        label, body = lines[i][:2]
        assert label.text == mark
        assert label.x0 == pytest.approx(18 * BP_PER_MM + indent * PT, abs=.04)
        assert body.x0 == pytest.approx(label.x0 + label.advance + 3 * PT, abs=.04)
        assert [label.size, body.size] == pytest.approx([size * PT] * 2, abs=.02)
        assert face(label) == ("NanumMyeongjoBold" if i == 2 else faces[i])
        assert face(body) == faces[i]
    assert len(lines) > 6 and text(lines[-2]) == "이어진문단"
    for chars in lines[4:-1]:
        assert chars[0].x0 == pytest.approx(lines[3][1].x0, abs=.04)
        assert face(chars[0]) == "NanumGothic" and chars[0].size == pytest.approx(13 * PT, abs=.02)
    assert text(lines[-1]) == "본문" and face(lines[-1][0]) == "NanumMyeongjo"
    assert lines[-1][0].size == pytest.approx(11 * PT, abs=.02)


def test_equal_labels_use_effective_face_size_and_section_rules(tmp_path):
    row = {"expr": "-", "indent_mm": 2, "gap_mm": 1, "font_pt": 15, "font": "nanum-gothic"}
    levels = [row, {**row, "mark_weight": "bold", "indent_origin": "margin"},
              {**row, "font_pt": 13, "mark_weight": "bold"}]
    rule = {"path": ["구간"], "role": "list", "values": {"font_pt": 17, "levels.1.expr": "!", "first_indent_mm": 4}}
    style = apply_style(Style(), {"list_levels": levels, "heading_font": "nanum-myeongjo", "section_rules": [rule]})
    source = "- 하나\n  - 둘\n    - 셋\n\n# 구간\n\n- 넷\n  - 다섯"
    lines = [chars for _, chars in baselines(typeset(parse_md(source), style, tmp_path))]
    assert [text(chars) for chars in lines] == ["-하나", "-둘", "-셋", "구간", "!넷", "-다섯"]
    for line, size, mark_font in zip(lines[:3] + lines[-2:], [15, 15, 13, 17, 17],
                                    ["NanumGothic", "NanumGothicBold", "NanumGothicBold", "NanumGothic", "NanumGothicBold"],
                                    strict=True):
        assert face(line[0]) == mark_font and face(line[1]) == "NanumGothic"
        assert [c.size for c in line[:2]] == pytest.approx([size * PT] * 2, abs=.02)
    assert lines[1][0].x0 == pytest.approx(lines[0][0].x0, abs=.04)
    assert lines[2][0].x0 == pytest.approx(lines[1][1].x0 + 2 * BP_PER_MM, abs=.04)
    mark, body = lines[-1][:2]
    assert mark.x0 == pytest.approx(20 * BP_PER_MM, abs=.04)
    assert body.x0 == pytest.approx(mark.x0 + mark.advance + 5 * BP_PER_MM, abs=.04)


def test_browser_opens_edits_and_exports_optional_row_fields(browser, server_url):
    rows = editor_bootstrap()["list_presets"]["legal"]
    saved = apply_style(Style(), {"list_levels": rows}).to_stored()
    saved["list_levels"][0].update(font="nanum-gothic", font_sp=16 * SP, weight="bold",
                                  mark_weight="normal", indent_origin="margin")
    with browser.new_context() as context:
        page = context.new_page()
        page.goto(server_url)
        open_settings(page)
        page.locator("#style summary").click()
        with page.expect_response("**/api/style/open") as opened:
            page.locator("#style-file").set_input_files({"name": "보고서.json", "mimeType": "application/json",
                                                       "buffer": json.dumps(saved).encode()})
        assert opened.value.status == 200
        expect(page.locator("#list-preset")).to_have_value("custom")
        page.get_by_role("group", name="1단계", exact=True).get_by_label("표지 표현식").fill("□")
        with page.expect_download() as out:
            page.locator("#save-style").click()
        saved["list_levels"][0]["expr"] = "□"
        assert json.loads(out.value.path().read_bytes()) == saved
