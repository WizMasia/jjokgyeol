import pytest

from app.article_edit import toggle_pin
from app.document import InputError, heading_tokens, parse_md
from app.paragraph_edit import edit_alignment
from app.style import SP_PER_PT, Style


RIGHT = "<!-- 쪽결 정렬=오른쪽 -->"
BEFORE = "<!-- 쪽결 space_before_sp=81921 -->"
AFTER = "<!-- 쪽결 space_after_sp=auto -->"
FRACTION = 1.2500152587890625  # 81921sp.


def edit(source, first=0, last=None, **patch):
    return edit_alignment(dict(source=source, first_line=first, last_line=first if last is None else last, **patch))


def test_mixed_markers_query_preserves_bytes_and_maps_content_lines():
    source = f"앞\r{BEFORE}\r\n{RIGHT}\n{AFTER}\r작성자\r\n둘째 줄\n\n끝"
    classic = dict(source=source, align="right", supported=True, message="")
    assert edit(source, 1, 5) == classic
    expected = dict(classic, start_line=4, space_before_pt=FRACTION, space_after_pt="auto")
    assert edit(source, 1, 5, spacing=True) == expected
    assert edit(source, 2, space_before_pt=FRACTION, space_after_pt="auto") == expected
    first, second, third = parse_md(source).blocks
    assert [b.inlines[0].text for b in (first, second, third)] == ["앞", "작성자 둘째 줄", "끝"]
    assert (second.align, second.space_before_sp, second.space_after_sp) == ("right", 81921, "auto")


@pytest.mark.parametrize(("value", "stored"), [(18, 1179648), (80, 5242880),
                                                  ("0.0000152587890625", 1), ("auto", "auto"), (None, None)])
def test_leading_edit_preserves_bom_mixed_endings_and_reports_legacy_shape(value, stored):
    source = "\ufeff첫 줄\r\n둘째 줄\r\n\r셋째 줄\n\n끝"
    result = edit(source, 1, leading_pt=value)
    assert result["source"].startswith("\ufeff")
    if stored is None:
        assert result["source"] == source
    else:
        marker = f"<!-- 쪽결 leading_sp={stored} -->\r\n"
        assert marker in result["source"]
        assert result["source"].replace(marker, "") == source
    expected = stored / SP_PER_PT if isinstance(stored, int) else stored
    assert result["leading_pt"] == expected
    assert edit(result["source"], result["start_line"], leading=True)["leading_pt"] == result["leading_pt"]
    if stored == 1179648:
        assert edit(result["source"], result["start_line"], leading_pt=None)["source"] == source
    assert edit(source, 1).keys() == {"source", "align", "supported", "message"}


def test_independent_edits_round_trip_a_pinned_heading_and_original_manuscript():
    source = "\ufeff제8조 목적[^m]\r둘째 줄\r\n---\n\r\n다음\n[^m]{메모}: 편집"
    marked = edit(source, 1, 2, align="right", space_before_pt=FRACTION, space_after_pt=0)
    row = marked["start_line"]
    assert marked["source"].startswith("\ufeff") and marked["source"].endswith(source[1:])
    assert marked["source"].splitlines()[row] == "제8조 목적[^m]"
    assert (marked["space_before_pt"], marked["space_after_pt"]) == (FRACTION, 0)
    doc = parse_md(marked["source"])
    heading, body = doc.blocks
    assert doc.memos == {"m": "편집"}
    assert heading.kind == "heading" and (heading.align, heading.space_before_sp, heading.space_after_sp) == ("right", 81921, 0)
    assert body.space_before_sp is None and body.space_after_sp is None
    (token, _), = heading_tokens(marked["source"])
    assert token.map == [row, row + 3]
    pinned = toggle_pin(marked["source"], row + 2, row + 2)
    assert pinned == marked["source"].replace("둘째 줄", "둘째 줄 {번호고정}")
    assert toggle_pin(pinned, row, row) == marked["source"]

    cleared = edit(marked["source"], row + 2, space_before_pt=None)
    assert (cleared["space_before_pt"], cleared["space_after_pt"], cleared["align"]) == (None, 0, "right")
    assert cleared["source"].splitlines()[cleared["start_line"]] == "제8조 목적[^m]"
    legacy = edit(cleared["source"], cleared["start_line"], align=None)
    assert legacy.keys() == {"source", "align", "supported", "message"}
    queried = edit(legacy["source"], 0, spacing=True)
    assert queried["space_after_pt"] == 0 and queried["space_before_pt"] is None and queried["align"] is None
    reset = edit(legacy["source"], queried["start_line"], space_after_pt=None)
    assert reset["source"] == source and reset["start_line"] == 0


def test_changing_one_field_preserves_other_markers_and_their_line_endings():
    source = f"앞\r{RIGHT}\n{BEFORE}\r\n{AFTER}\r다음"
    result = edit(source, 2, space_before_pt=0)
    assert result["source"] == source.replace("81921", "0")
    assert result["start_line"] == 4
    cleared = edit(result["source"], 1, align=None, space_before_pt=None, space_after_pt=None)
    assert cleared["source"] == "앞\r\r다음"
    assert cleared["start_line"] == 2
    assert len(parse_md(cleared["source"]).blocks) == 2


def test_last_marker_wins_per_field_and_only_changed_duplicates_are_removed():
    another = "<!-- 쪽결 space_before_sp=0 -->"
    source = f"{BEFORE}\r{AFTER}\r\n{another}\n{RIGHT}\n단락"
    assert edit(source, 0, spacing=True)["space_before_pt"] == 0
    assert edit(source, 0, space_before_pt=0)["source"] == source
    assert edit(source, 0, space_before_pt=None)["source"] == f"{AFTER}\r\n{RIGHT}\n단락"


@pytest.mark.parametrize("source,row", [
    (f"~~~\n{BEFORE}\n예시\n~~~", 2),
    (f"- 항목\n{AFTER}\n계속", 2),
    (f"- 항목\n\n{AFTER}\n  재개", 3),
    (f"> 인용\n{AFTER}\n계속", 2),
    (f"{AFTER}\n    코드", 1),
    (f"{BEFORE}\nA | B\n-- | --\n값 | 값", 1),
    (f"주[^a]\n{AFTER}\n[^a]: 각주", 2),
    (BEFORE, 0),
    ("하나\n\n둘", 1),
])
def test_ineligible_ranges_have_no_direct_spacing(source, row):
    result = edit(source, row, space_after_pt=72)
    assert not result["supported"] and result["message"] and result["source"] == source
    assert result["space_before_pt"] is result["space_after_pt"] is result["start_line"] is None
    assert all(b.space_before_sp is b.space_after_sp is None for b in parse_md(source).blocks)


@pytest.mark.parametrize("field", ["space_before_pt", "space_after_pt"])
@pytest.mark.parametrize("value", [-0.000001, 72.000001, False, [], {}, "", "bad", float("inf"), float("nan")])
def test_invalid_spacing_reports_the_supplied_field(field, value):
    with pytest.raises(InputError) as error:
        edit("내용", **{field: value})
    assert error.value.field == field


def test_response_reports_the_quantized_value_and_accepts_range_endpoints():
    manual = edit("단락", space_before_pt=0.1, space_after_pt=72)
    assert manual["space_before_pt"] == 6554 / 65536 and manual["space_after_pt"] == 72
    assert edit(manual["source"], manual["start_line"], spacing=True) == manual
    cleared = edit(manual["source"], 0, space_before_pt="auto", space_after_pt=None)
    assert (cleared["space_before_pt"], cleared["space_after_pt"]) == ("auto", None)


@pytest.mark.parametrize("value", [0, -1, 16384, "bad", True])
def test_invalid_leading_reports_its_field(value):
    with pytest.raises(InputError) as error:
        edit("내용", leading_pt=value)
    assert error.value.field == "leading_pt"


@pytest.mark.parametrize("value", [0, -1, 1073676289])
def test_stored_leading_rejects_out_of_range_sp(value):
    from app.document import paragraph_marker

    assert paragraph_marker(f"<!-- 쪽결 leading_sp={value} -->") is None


def test_leading_edit_round_trips_into_printed_document(tmp_path):
    from app.typeset import typeset
    from pdf_metrics import read_pages

    edited = edit("첫 줄 둘째 줄 셋째 줄 " * 8, 0, leading_pt=18)
    doc = parse_md(edited["source"])
    folder = tmp_path / "leading-edit"
    folder.mkdir()
    pages = read_pages(typeset(doc, Style(), folder))
    lines = [line for page in pages for line in page.lines if line.chars]
    assert lines[0].chars[0].baseline - lines[1].chars[0].baseline == pytest.approx(18 * 72 / 72.27, abs=.02)


def test_spacing_opt_in_requires_a_boolean():
    assert edit("글", spacing=False) == edit("글")
    with pytest.raises(InputError) as error:
        edit("글", spacing=1)
    assert error.value.field == "spacing"


@pytest.mark.parametrize("raw", ["-1", "4718593", "1.1", "true", "9" * 4500])
def test_malformed_spacing_marker_stays_literal(raw):
    marker = f"<!-- 쪽결 space_before_sp={raw} -->"
    block, = parse_md(marker + "\n글").blocks
    assert block.inlines[0].text == marker + " 글"
    assert block.space_before_sp is None


def test_spacing_query_without_overrides_is_a_noop():
    source = "앞\n\n문단\r\n다음 줄"
    assert edit(source, 3, spacing=True) == dict(source=source, supported=True, message="", align=None,
                                                start_line=2, space_before_pt=None, space_after_pt=None)
    assert edit(source, 3, align=None, space_after_pt=None)["source"] == source
