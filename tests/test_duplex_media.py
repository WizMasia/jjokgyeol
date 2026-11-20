import pytest

from test_reference_media import analyze, pdf
from test_running_heads_media import field, rows, scan_result


def test_pdf_first_alternating_and_common_faces_survive_single_page_retry():
    titles = ["Cover", "Even", "Odd", "Even", "Odd"]
    source = pdf([rows(title, f"- {n} -") for n, title in enumerate(titles, 17)])
    units = analyze(source, "pdf")["units"]
    for unit, face in zip(units, ["first", "even", "odd", "even", "odd"], strict=True):
        header = field(unit, "header", "left")
        assert header["suggested_face"] == face and header["variant"] == face
        footer = field(unit, "footer", "center")
        assert footer["value"] == "- {현재쪽} -" and footer["suggested_face"] == "default"
        assert "추정" in footer["evidence"]
        for c in unit["candidates"]:
            if c["role"] in ("footer", "header"):
                assert c["suggested_face"] == (face if c["role"] == "header" else "default")
            else:
                assert "suggested_face" not in c
    retried, = analyze(source, "pdf", page=4)["units"]
    for role, slot in (("header", "left"), ("footer", "center")):
        assert field(retried, role, slot) == field(units[3], role, slot)


def test_pdf_recommends_opposing_faces_for_mirrored_numbers_and_default_for_common_text():
    source = pdf([rows("2026 Report", str(n), x=310 if n % 2 else 20) for n in range(1, 5)])
    for unit in analyze(source, "pdf")["units"]:
        odd = unit["page"] % 2
        assert field(unit, "header", "left")["suggested_face"] == "default"
        number = field(unit, "footer", "right" if odd else "left")
        assert (number["suggested_face"], number["value"]) == ("odd" if odd else "even", "{현재쪽}")


def test_pdf_first_face_requires_evidence_from_later_odd_and_even_pages():
    source = pdf([rows("Cover", "1"), rows("Report", "2"), rows("Report", "3")])
    assert [field(u, "header", "left")["suggested_face"] for u in analyze(source, "pdf")["units"]] == [
        "first", "default", "default"]
    # A different line on page 1 could still be an ordinary odd-page header.
    sparse = pdf([rows("Cover", "1"), rows("Report", "2"), [("Body", 11, 36, 330)], rows("Report", "4")])
    for unit in analyze(sparse, "pdf")["units"]:
        for c in unit["candidates"]:
            if c["role"] == "header":
                assert c["suggested_face"] is None


def test_pdf_repetitions_among_conflicting_readings_do_not_imply_a_common_face():
    source = pdf([rows(title, f"- {n} -") for n, title in enumerate(["A", "B", "A", "A", "B"], 1)])
    for unit in analyze(source, "pdf")["units"]:
        header = field(unit, "header", "left")
        assert header["variant"] == "unknown" and header["suggested_face"] is None


def test_single_page_pdf_and_scan_require_face_selection_separately_from_number_options():
    pdf_page, = analyze(pdf([rows("Report", "- 17 -")]), "pdf")["units"]
    scan = scan_result("- 17 -")
    for unit in (pdf_page, scan):
        for c in unit["candidates"]:
            if c["role"] in ("header", "footer"):
                assert c["variant"] == "unknown" and c["suggested_face"] is None
    footer = field(scan, "footer", "center")
    assert footer["options"] == [{"kind": "literal", "value": "- 17 -"}, {"kind": "page", "value": "- {현재쪽} -"}]


def outer_headers(first_x, *, odd_pages=(3, 5), odd_y=484):
    positions = {1: ("Cover", first_x, 484), 2: ("Even", 20, 484), 4: ("Even", 20, 484)}
    positions.update({n: ("Odd", 316, odd_y) for n in odd_pages})
    pages = []
    for n in range(1, 6):
        lines = [("Body", 11, 36, 330)]
        if n in positions:
            title, x, y = positions[n]
            lines.append((title, 8, x, y))
        pages.append(lines)
    return pdf(pages)


@pytest.mark.parametrize("first_x", [20, 309])  # Cover and Odd have different ink widths at the right edge.
def test_mirrored_headers_allow_a_first_page_exception_in_either_slot(first_x):
    source = outer_headers(first_x)
    units = analyze(source, "pdf")["units"]
    for u, slot, face in zip(units, ["left" if first_x == 20 else "right", "left", "right", "left", "right"],
                             ["first", "even", "odd", "even", "odd"], strict=True):
        c = field(u, "header", slot)
        assert (c["variant"], c["suggested_face"]) == (face, face)
    retry, = analyze(source, "pdf", page=4)["units"]
    assert field(retry, "header", "left") == field(units[3], "header", "left")


@pytest.mark.parametrize("kwargs", [{"odd_pages": (3,)}, {"odd_y": 466}])
def test_sparse_or_vertically_unrelated_outer_headers_keep_the_face_open(kwargs):
    for unit in analyze(outer_headers(20, **kwargs), "pdf")["units"]:
        for c in unit["candidates"]:
            if c["role"] == "header":
                assert c["suggested_face"] is None
