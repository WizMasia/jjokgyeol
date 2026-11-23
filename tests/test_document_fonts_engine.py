"""Document-scoped font references in saved styles, rule proofs and model replay."""
import hashlib
import json
import subprocess
from functools import partial
from pathlib import Path
from time import monotonic

import pytest

from app.ai.decision import render_provider_choice
from app.document import InputError, parse_md
from app.document_fonts import use_document_fonts
from app.font_rights import inspect_face
from app.publication import FONT_DIR
from app.style import Style, apply_style, from_editor_style, from_stored_style
from app.typeset import typeset
from pdf_metrics import read_pages
from test_decision import SOURCE, STYLE
from test_publication import CONFIG, install, rewrite, start

DIGEST = "0123456789abcdef" * 4


def font_patch(family):
    return {
        "body_font": family,
        "heading_font": family,
        "text_styles": {role: {"font": family} for role in ("heading1", "note", "gloss")},
        "list_levels": [{"expr": "{숫자}.", "indent_mm": "0", "gap_mm": "2", "font": family}],
        "section_rules": [{"path": [], "role": role, "values": {"font": family}}
                          for role in ("body", "heading", "list", "header", "footer")],
    }


@pytest.mark.parametrize("family", [f"font-{DIGEST}-none", f"font-none-{DIGEST}", f"font-{DIGEST}-{DIGEST}"])
def test_standalone_style_preserves_unresolved_font_references(family):
    patch = font_patch(family)
    style = from_editor_style(patch)
    assert apply_style(Style(), patch) == style
    assert {key: style.to_editor()[key] for key in patch} == patch
    stored = json.loads(json.dumps(style.to_stored()))
    assert from_stored_style(stored).to_stored() == stored
    assert from_editor_style(from_stored_style(stored).to_editor()) == style


@pytest.mark.parametrize("bad", ["font-none-none", f"font-{DIGEST.upper()}-none", f"font-{DIGEST[:-1]}-none",
                                 f"font-{DIGEST}-none\n", f"font-{DIGEST}-../face", None, []])
def test_invalid_ref_rejected_at_each_style_field(bad):
    valid = font_patch(f"font-{DIGEST}-none")
    style = from_editor_style(valid)
    for payload, load in ((style.to_editor(), from_editor_style), (style.to_stored(), from_stored_style)):
        for key in ("body_font", "heading_font"):
            with pytest.raises(InputError) as exc:
                load({**payload, key: bad})
            assert exc.value.field == key
        for key, patch, path in (
            ("text_styles", {"note": {"font": bad}}, "text_styles.note.font"),
            ("list_levels", [{**payload["list_levels"][0], "font": bad}], "list_levels[0].font"),
            ("section_rules", [{"role": "header", "path": [], "values": {"font": bad}}], "section_rules[0].values.font"),
        ):
            with pytest.raises(InputError) as exc:
                load({**payload, key: patch})
            assert exc.value.field == path


def test_installed_default_font_still_requires_an_installed_family(install):
    rewrite(install, CONFIG, lambda config: config["defaults"].update(body_font=f"font-{DIGEST}-none"))
    result = start(install, "from app.style import Style")
    assert result.returncode != 0 and "defaults.body_font" in result.stderr


def face_names(pdf):
    return {char.text: char.font.split("+")[-1] for page in read_pages(pdf) for line in page.lines
            for char in line.chars}


def embedded_faces(pdf_path):
    rows = subprocess.check_output(["pdffonts", pdf_path], text=True).splitlines()[2:]
    assert rows and all(row.split()[-5:-2] == ["yes", "yes", "yes"] for row in rows)
    return {row.split()[0].split("+")[-1] for row in rows}


def test_rule_and_fixed_model_embed_the_same_job_font(tmp_path):
    # Unmodified google/fonts ofl/abel at 3b99d83d2625944fc0b8bd328d793fa819b92381.
    data = (Path(__file__).parent / "fixtures/fonts/abel/Abel-Regular.ttf").read_bytes()
    assert inspect_face(data)["remote"]
    digest = hashlib.sha256(data).hexdigest()
    style = apply_style(Style(), {**STYLE, "body_font": f"font-{digest}-none", "line_numbers": "left"})
    doc = parse_md(SOURCE + "\n\n가人⼈나 **굵은**")

    class ShortFirstLine:
        def predict(self, options, *, timeout):
            return min(options, key=lambda key: options[key][0]["end_glyph"])

    with use_document_fonts({digest: data}):
        pdf, status, reason = render_provider_choice(doc, style, tmp_path, monotonic() + 30, ShortFirstLine())
        native = tmp_path / "native"
        native.mkdir()
        _, native_status, native_reason = render_provider_choice(
            doc, style, native, monotonic() + 30, ShortFirstLine(), typesetter=partial(typeset, font_dir=FONT_DIR))
    assert (native_status, native_reason) == ("model", None)
    assert (status, reason) == ("model", None)
    assert pdf == (tmp_path / "replay/book.pdf").read_bytes()
    for name in ("measure", "replay"):
        job = tmp_path / name
        chosen = (job / "book.pdf").read_bytes()
        # Independent jobs with the native and web directory layouts must agree on every
        # glyph, line ending, margin number and page boundary for each decision path.
        assert read_pages(chosen) == read_pages((native / name / "book.pdf").read_bytes())
        glyphs = face_names(chosen)
        assert glyphs["D"] == "Abel-Regular"
        assert glyphs["굵"] == "NanumMyeongjoBold"
        assert {"Abel-Regular", "NanumMyeongjoBold"} <= embedded_faces(job / "book.pdf")
        extracted = subprocess.check_output(["pdftotext", "-raw", "-", "-"], input=chosen).decode()
        assert "가人⼈나" in "".join(extracted.split())
        assert (job / "fonts" / f"doc-{digest}.ttf").read_bytes() == data
        assert not (job / "fonts").is_symlink()
    after = tmp_path / "after"
    after.mkdir()
    assert face_names(typeset(doc, style, after))["D"] == "NanumMyeongjo"


@pytest.fixture
def sparse_otf():
    from test_font_rights import font

    # The generated OFL face contains one letter and no digits or brackets.
    data = font(0, license="OFL-1.1", cff=True)
    assert inspect_face(data)["pdf"]
    return data


def test_otf_bold_only_nested_face_and_missing_glyph(tmp_path, sparse_otf):
    data = sparse_otf
    digest = hashlib.sha256(data).hexdigest()
    family = f"font-none-{digest}"
    style = apply_style(Style(), {"section_rules": [{"path": [], "role": "body", "values": {"font": family}}]})
    with use_document_fonts({digest: data}):
        pdf = typeset(parse_md("R **A한人⼈**"), style, tmp_path, font_dir=FONT_DIR)
    glyphs = face_names(pdf)
    assert glyphs["R"] == "NanumMyeongjo"
    assert glyphs["A"] == "Paper-Bold"
    assert glyphs["한"] == "NanumMyeongjoBold"
    assert "Paper-Bold" in embedded_faces(tmp_path / "book.pdf")
    assert (tmp_path / "fonts" / f"doc-{digest}.otf").read_bytes() == data
    assert subprocess.check_output(["pdftotext", "-raw", "-", "-"], input=pdf).decode().strip() == "R A한人⼈"


def test_missing_glyph_label_and_margin_digits_without_ascii_in_primary(tmp_path, sparse_otf):
    data = sparse_otf
    digest = hashlib.sha256(data).hexdigest()
    style = from_editor_style({"body_font": f"font-{digest}-none", "line_numbers": "left"})
    with use_document_fonts({digest: data}):
        pdf = typeset(parse_md("A🧪뒤"), style, tmp_path)
    text = subprocess.check_output(["pdftotext", "-raw", "-", "-"], input=pdf).decode()
    assert "".join(text.split()) == "1A[U+1F9EA]뒤"


def test_locked_digest_resolves_without_upload(tmp_path):
    data = (FONT_DIR / "NanumGothicBold.ttf").read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    style = from_editor_style({"body_font": f"font-none-{digest}"})
    assert face_names(typeset(parse_md("**굵은**"), style, tmp_path))["굵"] == "NanumGothicBold"
