import json
from copy import deepcopy
from decimal import Decimal

import pytest


def test_order_and_note():
    from app.document import parse_md

    doc = parse_md("## 제목\n\n앞 *강조 **굵게** 끝* 뒤[^지도].\n다음 줄\n\n[^지도]: 주 내용")
    assert [(b.kind, b.level) for b in doc.blocks] == [("heading", 2), ("paragraph", None)]
    assert doc.blocks[0].inlines[0].text == "제목"
    nodes = doc.blocks[1].inlines
    assert [n.kind for n in nodes] == ["text", "emphasis", "text", "note_ref", "text"]
    assert [nodes[0].text, nodes[2].text, nodes[4].text] == ["앞 ", " 뒤", ". 다음 줄"]
    first, strong, last = nodes[1].children
    assert (first.text, strong.kind, strong.children[0].text, last.text) == ("강조 ", "strong", "굵게", " 끝")
    assert nodes[3].id == "지도"
    assert doc.footnotes == {"지도": "주 내용"}


def test_ideographic_paragraph_indent():
    from app.document import parse_md

    plain, emph = parse_md("　첫 문장\n\n　　**둘째** 문장").blocks
    assert plain.inlines[0].text == "　첫 문장"
    assert [(i.kind, i.text) for i in emph.inlines[:2]] == [("text", "　　"), ("strong", None)]


def test_literal_or_rejected():
    from app.document import InputError, parse_md

    literal = r"한글 \input{비공개} \% & <b>글</b> &#35; $x$"
    doc = parse_md(f"[^id]: {literal}\n\n{literal}[^id]")
    assert doc.blocks[0].inlines[0].text == literal
    assert doc.footnotes["id"] == literal
    for source in ("본문[^비공개]", "[^비공개]: 하나\n[^비공개]: 둘", None):
        with pytest.raises(InputError) as caught:
            parse_md(source)
        error = caught.value
        assert (error.code, error.field) == ("invalid_input", "source")
        assert error.message and "비공개" not in str(error)


def test_style_atomic():
    from app.document import InputError
    from app.style import Style, apply_style

    current = Style()
    before = deepcopy(current)
    assert json.loads(json.dumps(current.to_stored())) == {
        "page_sp": [27597261, 39158276], "margins_sp": [3729360, 3356424, 3729360, 3356424],
        "body_sp": 720896, "leading_sp": 1114112, "body_font": "nanum-myeongjo",
        "heading_font": "nanum-gothic", "ko_tracking_per_10000": 0, "space_per_10000": [10000, 10000],
        "first_indent_sp": 0, "rest_indent_sp": 0,
    }
    patch = {"page_mm": [140, 210], "margins_mm": [20, 75, 20, 75], "body_pt": 16}
    with pytest.raises(InputError) as caught:
        apply_style(current, patch)
    assert caught.value.field == "margins_mm"
    assert current == before
    patch.update(margins_mm=[20, 10, 20, 10], leading_pt=24, ko_tracking_pct=-7, space_pct=[70, 130])
    new = apply_style(current, patch)
    patch["margins_mm"][1] = 75
    assert (new.body_sp, new.leading_sp, new.ko_tracking_per_10000, new.space_per_10000) == (1048576, 1572864, -700, (7000, 13000))
    assert new.margins_sp == (3729360, 1864680, 3729360, 1864680)
    assert current == before


@pytest.mark.parametrize(("patch", "field"), [
    ({"body_font": "/private/비공개"}, "body_font"),
    ({"heading_font": ["bad"]}, "heading_font"),
    ({"body_pt": float("nan")}, "body_pt"),
    ({"leading_pt": float("inf")}, "leading_pt"),
    ({"body_pt": True}, "body_pt"),
    ({"body_pt": 20000}, "body_pt"),
    ({"leading_pt": 20000}, "leading_pt"),
    ({"page_mm": [9000, 210]}, "page_mm"),
    ({"ko_tracking_pct": 1e100}, "ko_tracking_pct"),
    ({"body_pt": 10**1000}, "body_pt"),
    ({"page_mm": [0, 120]}, "page_mm"),
    ({"page_mm": [148]}, "page_mm"),
    ({"margins_mm": [-1, 2, 3, 4]}, "margins_mm"),
    ({"margins_mm": [105, 0, 105, 0]}, "margins_mm"),
    ({"space_pct": [100, 90]}, "space_pct"),
    ({"space_pct": [60, 130]}, "space_pct"),
    ({"ko_tracking_pct": float("-inf")}, "ko_tracking_pct"),
    ({"비공개": 1}, "style"),
    ([], "style"),
])
def test_style_errors(patch, field):
    from app.document import InputError
    from app.style import Style, apply_style

    with pytest.raises(InputError) as caught:
        apply_style(Style(), patch)
    assert (caught.value.code, caught.value.field) == ("invalid_input", field)
    assert caught.value.message and "비공개" not in str(caught.value)


def test_patch_reports_each_invalid_field():
    from app.document import InputError
    from app.style import Style, apply_style

    with pytest.raises(InputError) as caught:
        apply_style(Style(), {"body_pt": 0, "body_font": "../font"})
    assert set(caught.value.errors) == {"body_pt", "body_font"}


def test_fractional_positive_sizes_remain_available():
    from app.style import Style, apply_style

    style = apply_style(Style(), {"body_pt": 0.5, "leading_pt": 0.75})
    assert (style.body_sp, style.leading_sp) == (32768, 49152)


@pytest.mark.parametrize("source", ["본문\x00끝", "각주[^주]\n\n[^주]: \ud800"])
def test_invalid_manuscript_characters_report_source(source):
    from app.document import InputError, parse_md

    with pytest.raises(InputError) as caught:
        parse_md(source)
    assert caught.value.field == "source"


@pytest.mark.parametrize(("pct", "expected"), [
    ("-7.245", -725), (7.245, 725), (Decimal("-7.2449999999999999999999999999999999999"), -724),
    ("0.005", 1), ("-0.005", -1), ("7e-10000000", 0),
])
def test_exact_tracking_round_trip(pct, expected):
    from app.style import Style, apply_style, from_stored_style

    style = apply_style(Style(), {
        "ko_tracking_pct": pct, "body_pt": "11.00000762939453125", "leading_pt": "17",
        "first_indent_mm": "2", "rest_indent_mm": "+1", "space_pct": ["70.005", "129.995"],
    })
    assert (style.ko_tracking_per_10000, style.space_per_10000) == (expected, (7001, 13000))
    assert (style.body_sp, style.first_indent_sp, style.rest_indent_sp) == (720897, 372936, 186468)
    for _ in range(3):
        fields = json.loads(json.dumps(style.to_stored()), parse_int=Decimal)
        assert from_stored_style(fields) == style
        style = from_stored_style(fields)


@pytest.mark.parametrize(("patch", "field"), [
    ({"space_pct": ["69.999999999999999999999", 130]}, "space_pct"),
    ({"space_pct": [70, "130.00001"]}, "space_pct"),
    ({"space_pct": ["90.004", "90.003"]}, "space_pct"),
    ({"body_pt": "1279.0000001"}, "body_pt"),
    ({"margins_mm": ["-1e-100", 18, 20, 18]}, "margins_mm"),
    ({"body_pt": " 11 "}, "body_pt"), ({"body_pt": "1_1"}, "body_pt"),
    ({"first_indent_mm": "-1e-80"}, "first_indent_mm"),
    ({"rest_indent_mm": "-0.01"}, "rest_indent_mm"),
    ({"page_mm": ["0.00000001", 210], "margins_mm": [0, 0, 0, 0]}, "page_mm"),
    ({"page_mm": [1, 210], "margins_mm": [0, "0.99999999", 0, 0]}, "margins_mm"),
    ({"first_indent_mm": "112", "page_mm": [148, 210], "margins_mm": [20, 18, 20, 18]}, "first_indent_mm"),
    ({"rest_indent_mm": "0.99999999", "page_mm": [1, 210], "margins_mm": [20, 0, 20, 0]}, "rest_indent_mm"),
    ({"rest_indent_mm": "-5000", "page_mm": [5000, 210]}, "rest_indent_mm"),
    ({"ko_tracking_pct": "1e400"}, "ko_tracking_pct"),
    ({"body_pt": 100, "ko_tracking_pct": 16000}, "ko_tracking_pct"),
    ({"body_pt": 100, "ko_tracking_pct": "10239.374"}, None),
    ({"body_pt": 100, "ko_tracking_pct": "10239.375"}, "ko_tracking_pct"),
])
def test_original_and_rounded_style_bounds(patch, field):
    from app.document import InputError
    from app.style import Style, apply_style

    if field is None:
        assert apply_style(Style(), patch).ko_tracking_per_10000 == 1023937
    else:
        with pytest.raises(InputError) as caught:
            apply_style(Style(), patch)
        assert caught.value.field == field


@pytest.mark.parametrize(("change", "field"), [
    ({"body_sp": None}, "body_sp"), ({"leading_sp": "17"}, "leading_sp"),
    ({"first_indent_sp": -1}, "first_indent_sp"), ({"rest_indent_sp": -1}, "rest_indent_sp"),
    ({"first_indent_sp": Decimal("1.5")}, "first_indent_sp"), ({"body_sp": True}, "body_sp"),
    ({"page_sp": [2**53+1, 42]}, "page_sp"), ({"space_per_10000": [6999, 13000]}, "space_per_10000"),
    ({"ko_tracking_per_10000": 2**63-1}, "ko_tracking_per_10000"),
    ({"rest_indent_sp": 27597261}, "rest_indent_sp"), ({"body_pt": 11}, "style"),
])
def test_stored_style_requires_exact_integer_fields(change, field):
    from app.document import InputError
    from app.style import Style, from_stored_style

    fields = Style().to_stored()
    with pytest.raises(InputError) as caught:
        from_stored_style({**fields, **change})
    assert caught.value.field == field
    del fields["leading_sp"]
    with pytest.raises(InputError) as missing:
        from_stored_style(fields)
    assert missing.value.field == "leading_sp"


def test_worker_checks_original_json_decimal_before_rounding(tmp_path):
    import subprocess
    from app.jobs import INVALID_INPUT, WORKER_CMD

    body = '{"source":"교정","style":{"space_pct":[69.99999999999999999999,130]}}'
    (tmp_path / "request.json").write_text(body, encoding="utf-8")
    process = subprocess.run(WORKER_CMD, cwd=tmp_path, capture_output=True, timeout=10)
    assert process.returncode == INVALID_INPUT
    assert json.loads((tmp_path / "invalid.json").read_text())[0] == "space_pct"


def test_page_accepts_existing_upper_limit_and_one_sp_usable_width():
    from app.style import Style, apply_style, from_stored_style

    for width_mm, expected_sp in [(5757, 1073496167), ("0.0000054", 1)]:
        style = apply_style(Style(), {"page_mm": [width_mm, 210], "margins_mm": [0, 0, 0, 0]})
        assert style.page_sp[0] == expected_sp
        assert from_stored_style(style.to_stored()) == style


def test_rounded_heading_size_bounds_tracking_product():
    from app.document import InputError
    from app.style import Style, from_stored_style

    # Lua rounds H1 at 327681sp from 524289.6 to 524290sp before tracking.
    fields = {**Style().to_stored(), "body_sp": 327681, "ko_tracking_per_10000": 20478687}
    with pytest.raises(InputError) as caught:
        from_stored_style(fields)
    assert caught.value.field == "ko_tracking_per_10000"


def test_safe_tracking_at_rounded_heading_limit_reopens_in_editor_units():
    from app.style import Style, apply_style, from_stored_style

    # At this size the engine rounds the heading down to 524291sp.
    fields = {**Style().to_stored(), "body_sp": 327682, "ko_tracking_per_10000": 20478632}
    style = from_stored_style(fields)
    assert apply_style(style, {}) == style
    assert apply_style(Style(), {"body_pt": "5.000030517578125", "ko_tracking_pct": "204786.32"}) == style


def test_zero_raw_width_is_rejected_even_if_stored_width_would_be_one_sp():
    from app.document import InputError
    from app.style import Style, apply_style

    with pytest.raises(InputError) as error:
        apply_style(Style(), {"page_mm": ["0.000014", 100], "margins_mm": [0, "0.000007", 0, "0.000007"]})
    assert error.value.field == "margins_mm"


def test_reports_errors_from_before_and_after_rounding():
    from app.document import InputError
    from app.style import Style, apply_style

    with pytest.raises(InputError) as error:
        apply_style(Style(), {"body_font": "unknown", "page_mm": ["0.00000001", 100], "margins_mm": [0, 0, 0, 0]})
    assert set(error.value.errors) == {"body_font", "page_mm"}


@pytest.mark.parametrize("suffix", ["원문", '"인용문"', " 이어지는 글"])
def test_padded_bold_label_preserves_spaces_and_following_face(suffix):
    from app.document import Inline, parse_md

    source = "- **① 점검 *결과*:  **" + suffix
    block, = parse_md(source).blocks
    assert block.inlines == [
        Inline("strong", children=[Inline("text", "① 점검 "), Inline("emphasis", children=[Inline("text", "결과")]),
                                   Inline("text", ":  ")]),
        Inline("text", suffix),
    ]


def test_padded_bold_leaves_valid_pairs_and_literal_stars_intact():
    from app.document import Inline, parse_md

    source = r"\*\*별: \*\* / **기존: **중첩** 끝** / **항목: **내용 **끝**"
    block, = parse_md(source).blocks
    assert block.inlines == [
        Inline("text", "**별: ** / "),
        Inline("strong", children=[Inline("text", "기존: "), Inline("strong", children=[Inline("text", "중첩")]),
                                   Inline("text", " 끝")]),
        Inline("text", " / "), Inline("strong", children=[Inline("text", "항목: ")]),
        Inline("text", "내용 "), Inline("strong", children=[Inline("text", "끝")]),
    ]
    for text in ["**열림\n다른 줄: **본문", "** 홀로: **본문", "**열린 채", "2 ** 3 ** 4"]:
        assert parse_md(text).blocks[0].inlines == [Inline("text", text.replace("\n", " "))]


def test_padded_label_cannot_start_in_a_formula_or_swallow_an_existing_bold_span():
    from app.document import Inline, parse_md

    formula = "계산 2**3, 결과: ** 8"
    assert parse_md(formula).blocks[0].inlines == [Inline("text", formula)]
    source = "- **가: **굵게** 뒤: **본문"
    assert parse_md(source).blocks[0].inlines == [
        Inline("text", "**가: "), Inline("strong", children=[Inline("text", "굵게")]), Inline("text", " 뒤: **본문"),
    ]


@pytest.mark.parametrize("middle", ["***굵게***", "**기울임*"])
def test_partly_or_fully_claimed_stars_end_padded_label_recovery(middle):
    from app.document import parse_md

    spans = parse_md(f"- **가: {middle} 뒤: **본문").blocks[0].inlines
    assert spans[0].kind == "text" and spans[0].text.startswith("**가: ")
    assert spans[-1].text == " 뒤: **본문"
