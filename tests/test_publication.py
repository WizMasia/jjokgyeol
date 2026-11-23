"""Exercise the installed publication settings without mutating a running app."""

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
FONTS = [{"id": "nanum-myeongjo", "label": "나눔명조"}, {"id": "nanum-gothic", "label": "나눔고딕"}]


def test_public_interfaces_use_locked_fonts_and_round_trip():
    from app.publication import FONT_DIR, engine_policy
    from app.style import Style, apply_style, editor_bootstrap, from_stored_style
    from app.style_files import editor_values

    assert FONT_DIR == Path("/usr/share/fonts/truetype/nanum")
    expected = {
        "font_order": ["nanum-myeongjo", "nanum-gothic"],
        "fonts": {
            "nanum-myeongjo": {"regular": "NanumMyeongjo.ttf", "bold": "NanumMyeongjoBold.ttf"},
            "nanum-gothic": {"regular": "NanumGothic.ttf", "bold": "NanumGothicBold.ttf"},
        },
        "heading_per_10000": [16000, 15000, 14000, 13000, 12000, 11000],
        "heading_leading_per_10000": 12500,
    }
    assert engine_policy() == expected
    engine_policy()["fonts"]["nanum-gothic"].clear()
    assert engine_policy() == expected
    style = Style()
    boot = editor_bootstrap()
    assert boot["defaults"] == style.to_editor() and boot["fonts"] == FONTS
    assert style.to_editor()["body_pt"] == "11"
    assert editor_values(style) == style.to_editor()
    stored = json.loads(json.dumps(style.to_stored()))
    assert stored["body_sp"] == 720896 and stored["space_per_10000"] == [10000, 10000]
    assert from_stored_style(stored) == apply_style(style, style.to_editor()) == style


def test_paragraph_layout_round_trip_and_new_document_default():
    from app.style import Style, apply_style, editor_bootstrap, from_stored_style

    boot = editor_bootstrap()
    assert boot["new_paragraph_layout"] == "justified"
    assert boot["new_spacing"] == {"ko_tracking_bounds_pct": ["-15", "15"], "space_pct": ["70", "130"]}
    legacy = Style()
    assert "paragraph_layout" not in boot["defaults"]
    assert "paragraph_layout" not in legacy.to_stored()
    assert "paragraph_layout" not in from_stored_style(legacy.to_stored()).to_editor()
    chosen = apply_style(legacy, {"paragraph_layout": boot["new_paragraph_layout"]})
    assert chosen.to_editor()["paragraph_layout"] == chosen.to_stored()["paragraph_layout"] == "justified"
    assert from_stored_style(chosen.to_stored()) == apply_style(legacy, chosen.to_editor()) == chosen
    assert apply_style(chosen, {"body_pt": 16}).paragraph_layout == "justified"


@pytest.mark.parametrize("value", [None, False, "", "legacy", "BALANCED", ["balanced"]])
def test_invalid_paragraph_layout_identifies_field(value):
    from app.document import InputError
    from app.style import Style, apply_style, from_stored_style

    with pytest.raises(InputError) as editor:
        apply_style(Style(), {"paragraph_layout": value})
    with pytest.raises(InputError) as stored:
        from_stored_style(dict(Style().to_stored(), paragraph_layout=value))
    assert editor.value.field == stored.value.field == "paragraph_layout"


def test_new_style_keeps_heading_sizes():
    from app.style import Style, apply_style, editor_bootstrap, from_stored_style

    boot = editor_bootstrap()
    legacy = Style()
    assert boot["defaults"] == legacy.to_editor()
    assert "text_styles" not in from_stored_style(legacy.to_stored()).to_editor()
    seed = boot["new_text_styles"]
    assert list(seed) == [f"heading{i}" for i in range(1, 7)]
    assert all(row.keys() == {"font_pt"} for row in seed.values())
    new = apply_style(legacy, {"text_styles": seed})
    resized = apply_style(new, {"body_pt": 16})
    saved = resized.to_stored()["text_styles"]
    assert saved == new.to_stored()["text_styles"]
    assert saved["heading1"] == {"font_sp": 1153434}
    edited = apply_style(resized, {"text_styles": {"heading1": {"font_pt": 18}}})
    assert edited.to_stored()["text_styles"] == {**saved, "heading1": {"font_sp": 1179648}}
    assert apply_style(legacy, edited.to_editor()) == from_stored_style(edited.to_stored()) == edited
    single = apply_style(legacy, {"text_styles": {"heading4": {"font_pt": 12}}})
    assert apply_style(single, {"body_pt": 16}).to_stored()["text_styles"] == {"heading4": {"font_sp": 786432}}


def test_role_patch_preserves_other_properties():
    from app.style import Style, apply_style, from_stored_style

    original = {"note": {"color": "#AbCdEf", "weight": "bold", "font": "nanum-gothic"},
                "body": {"slant": "italic"}, "gloss": {"font_pt": "11.00000762939453125"}}
    style = apply_style(Style(), {"text_styles": original})
    edited = apply_style(style, {"text_styles": {"note": {"weight": "normal"}}})
    stored = edited.to_stored()
    assert stored["text_styles"] == {**original, "note": {**original["note"], "weight": "normal"},
                                     "gloss": {"font_sp": 720897}}
    assert apply_style(Style(), from_stored_style(stored).to_editor()) == edited
    assert style.to_editor()["text_styles"]["note"]["weight"] == "bold"


@pytest.mark.parametrize(("role", "key", "value"), [
    ("heading0", "font", "nanum-gothic"), ("heading6", "font", "missing"), ("note", "color", "#ffeezz"),
    ("gloss", "weight", "heavy"), ("body", "slant", "oblique"), ("note", "other", 0),
    ("heading3", "font_pt", "0.00001525878906249999999999999999"), ("note", "font_pt", 2048),
    ("body", "font", "nanum-gothic"), ("body", "font_pt", 12), ("note", "font_sp", 65536),
])
def test_invalid_text_property_identifies_field(role, key, value):
    from app.document import InputError
    from app.style import Style, apply_style

    with pytest.raises(InputError) as exc:
        apply_style(Style(), {"text_styles": {role: {key: value}}})
    assert exc.value.field == (f"text_styles.{role}" if role == "heading0" else f"text_styles.{role}.{key}")


def test_invalid_patch_reports_roles_and_preserves_current_style():
    from app.document import InputError
    from app.style import Style, apply_style

    current = apply_style(Style(), {"text_styles": {"heading1": {"weight": "bold"}}})
    before = current.to_stored()
    with pytest.raises(InputError) as exc:
        apply_style(current, {"text_styles": {"heading1": {"weight": "normal", "color": "red"},
                                              "note": {"font_pt": False}}, "body_pt": 0})
    assert set(exc.value.errors) == {"text_styles.heading1.color", "text_styles.note.font_pt", "body_pt"}
    assert current.to_stored() == before


def test_tracking_bounds_round_trip():
    from app.document import InputError
    from app.style import Style, apply_style, from_stored_style

    style = apply_style(Style(), {"ko_tracking_pct": -7, "ko_tracking_bounds_pct": [-7, 0]})
    saved = style.to_stored()
    assert (saved["ko_tracking_per_10000"], saved["ko_tracking_bounds_per_10000"]) == (-700, [-700, 0])
    assert from_stored_style(saved) == apply_style(Style(), style.to_editor()) == style
    for patch, field in [
        ({"ko_tracking_pct": "-7.001"}, "ko_tracking_pct"),
        ({"ko_tracking_bounds_pct": ["-7", "-7.001"]}, "ko_tracking_bounds_pct"),
        ({"ko_tracking_bounds_pct": [-6, 1]}, "ko_tracking_bounds_pct"),
        ({"body_pt": 100, "ko_tracking_bounds_pct": [-7, "10239.375"]}, "ko_tracking_bounds_pct"),
    ]:
        with pytest.raises(InputError) as exc:
            apply_style(style, patch)
        assert exc.value.field == field
        assert style.to_stored() == saved
    for bounds in [["-10239.374", 0], [0, "10239.374"]]:
        safe = apply_style(Style(), {"body_pt": 100, "ko_tracking_bounds_pct": bounds})
        assert from_stored_style(safe.to_stored()) == safe


@pytest.mark.parametrize("role", ["heading1", "note"])
@pytest.mark.parametrize("bounds", [(-17000, 0), (0, 17000)])
def test_tracking_endpoints_must_fit_document_sizes(role, bounds):
    from app.document import InputError
    from app.style import Style, apply_style, from_stored_style

    patch = {"text_styles": {role: {"font_pt": 100}}}
    style = apply_style(Style(), patch)
    for base, edit in [(style, {}), (Style(), patch)]:
        with pytest.raises(InputError) as exc:
            apply_style(base, {**edit, "ko_tracking_bounds_pct": bounds})
        assert exc.value.field == "ko_tracking_bounds_pct"
    with pytest.raises(InputError) as exc:
        from_stored_style(dict(style.to_stored(), ko_tracking_bounds_per_10000=[n * 100 for n in bounds]))
    assert exc.value.field == "ko_tracking_bounds_per_10000"


def test_requested_gloss_size_round_trips_with_bounds_that_fit_surrounding_text():
    from app.document import InputError
    from app.style import Style, apply_style, from_stored_style

    style = apply_style(Style(), {"body_pt": 11, "text_styles": {"gloss": {"font_pt": 100}},
                                  "ko_tracking_bounds_pct": [-17000, 17000]})
    assert style.to_editor()["text_styles"]["gloss"] == {"font_pt": "100"}
    assert style.to_stored()["text_styles"]["gloss"] == {"font_sp": 6553600}
    assert from_stored_style(style.to_stored()) == style
    # At 100000% the derived 17.6pt heading exceeds TeX's limit; the 11pt body still fits.
    with pytest.raises(InputError) as exc:
        apply_style(style, {"ko_tracking_bounds_pct": [0, 100000]})
    assert exc.value.field == "ko_tracking_bounds_pct"


@pytest.mark.parametrize(("size", "bounds"), [(100, [-17000, 0]), (100, [0, 17000]), (128, [0, "12799.215"])])
def test_section_tracking_error_identifies_edit(size, bounds):
    from app.document import InputError
    from app.style import Style, apply_style, from_stored_style

    section = {"section_rules": [{"path": ["장"], "role": "body", "values": {"font_pt": size}}]}
    bounds_patch = {"ko_tracking_bounds_pct": bounds}
    with_section = apply_style(Style(), section)
    with_bounds = apply_style(Style(), bounds_patch)
    for current, patch, field in [
        (with_section, bounds_patch, "ko_tracking_bounds_pct"),
        (with_bounds, section, "section_rules[0].values.font_pt"),
        (Style(), {**section, **bounds_patch}, "section_rules[0].values.font_pt"),
    ]:
        with pytest.raises(InputError) as exc:
            apply_style(current, patch)
        assert exc.value.field == field

    stored = {**with_bounds.to_stored(), "section_rules": with_section.to_stored()["section_rules"]}
    with pytest.raises(InputError) as exc:
        from_stored_style(stored)
    assert exc.value.field == "section_rules[0].values.font_sp"


@pytest.mark.parametrize(("patch", "field"), [
    ({"text_styles": {"제목": {"color": "#17365D"}}}, "text_styles.제목"),
    ({"text_styles": {"제목\n": {"color": "#17365D"}}}, "text_styles"),
    ({"text_styles": {"note": {"글꼴\t": 12}}}, "text_styles.note"),
    ({"section_rules": [{"path": [], "role": "body", "values": {"글꼴\t": 12}}]}, "section_rules[0].values"),
])
def test_unsupported_names_have_printable_validation_paths(patch, field):
    from app.document import InputError
    from app.style import Style, apply_style, from_stored_style

    for load in (lambda: apply_style(Style(), patch), lambda: from_stored_style({**Style().to_stored(), **patch})):
        with pytest.raises(InputError) as exc:
            load()
        assert exc.value.field == field
        assert all(path.isprintable() and path.encode("utf-8") for path in exc.value.errors)


def test_bounds_use_effective_heading_and_unrounded_explicit_size():
    from app.document import InputError
    from app.style import Style, apply_style

    small = {f"heading{i}": {"font_pt": 1} for i in range(1, 7)}
    style = apply_style(Style(), {"text_styles": small, "body_pt": 100, "ko_tracking_bounds_pct": [0, 16383]})
    assert style.to_editor()["ko_tracking_bounds_pct"] == ["0", "16383"]
    with pytest.raises(InputError) as exc:
        apply_style(style, {"text_styles": {"heading2": {"font_pt": "100.00000000000000000001"}}})
    assert exc.value.field == "ko_tracking_bounds_pct"


@pytest.mark.parametrize(("patch", "field"), [
    ({"text_styles": {"note": {"font_sp": "65536"}}}, "text_styles.note.font_sp"),
    ({"text_styles": {"heading1": {"font_pt": 18}}}, "text_styles.heading1.font_pt"),
    ({"ko_tracking_bounds_per_10000": [0, 0.5]}, "ko_tracking_bounds_per_10000"),
    ({"ko_tracking_bounds_per_10000": [0, 2**53]}, "ko_tracking_bounds_per_10000"),
])
def test_stored_typography_requires_exact_units(patch, field):
    from app.document import InputError
    from app.style import Style, from_stored_style

    with pytest.raises(InputError) as exc:
        from_stored_style({**Style().to_stored(), **patch})
    assert exc.value.field == field


@pytest.fixture
def install(tmp_path):
    dest = tmp_path / "install"
    shutil.copytree(ROOT / "app", dest / "app", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "runtime.lock", dest)
    return dest


def start(install, script):
    # Isolated interpreter and a different cwd, as in the render worker.
    return subprocess.run(
        [sys.executable, "-IB", "-c", "import sys; sys.path.insert(0, sys.argv[1])\n" + script, str(install)],
        cwd=install.parent, capture_output=True, text=True, timeout=10,
    )


def rewrite(install, filename, edit):
    path = install / filename
    data = json.loads(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


CONFIG = "app/config/publication.json"


def test_changed_install_controls_defaults_limits_and_existing_files(install):
    def edit(config):
        config["defaults"].update(body_pt=13, body_font="nanum-gothic")
        config["page_presets_mm"] = {"교정판": [124.5, 192]}
        config["new_paragraph_layout"] = None
        config["fonts"][0]["label"] = "책 본문"
        config["space_pct_range"] = [75, 120]
        config["heading_per_10000"][1] = 20000
        config["text_defaults"].update(note_pt=9.25, gloss_per_10000=7500, color="#123456")
    rewrite(install, CONFIG, edit)
    result = start(install, """
from app.document import InputError
from app.publication import engine_policy
from app.style import Style, apply_style, from_stored_style, editor_bootstrap
s = Style()
assert (s.body_sp, s.body_font) == (851968, "nanum-gothic")
boot = editor_bootstrap()
assert boot["defaults"]["body_pt"] == "13"
assert boot["page_presets_mm"] == {"교정판": ["124.5", "192"]}
assert boot["new_paragraph_layout"] is None and "paragraph_layout" not in boot["defaults"]
assert boot["new_text_styles"]["heading2"] == {"font_pt": "26"}
assert boot["heading_per_10000"][1] == 20000
assert boot["heading_leading_per_10000"] == 12500
assert boot["text_defaults"] == {"color": "#123456", "weight": "normal", "heading_weight": "bold",
                                 "slant": "normal", "note_pt": "9.25", "gloss_per_10000": 7500}
assert editor_bootstrap()["fonts"][0]["label"] == "책 본문"
assert engine_policy()["heading_per_10000"][1] == 20000
old = dict(s.to_stored(), body_sp=720896, body_font="nanum-myeongjo")
assert from_stored_style(old).to_stored() == old
for patch, field in [({"space_pct": [75, 121]}, "space_pct"), ({"body_pt": 1024}, "body_pt"),
                     ({"body_pt": 100, "ko_tracking_pct": 9000}, "ko_tracking_pct")]:
    try:
        apply_style(s, patch)
    except InputError as e:
        assert e.field == field
    else:
        raise AssertionError(field)
""")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(("file", "edit", "field"), [
    (CONFIG, lambda c: c["defaults"].pop("first_indent_mm"), "first_indent_mm"),
    (CONFIG, lambda c: c["defaults"].update(body_px=11), "body_px"),
    (CONFIG, lambda c: c["defaults"].update(body_pt=True), "body_pt"),
    (CONFIG, lambda c: c["defaults"].update(margins_mm=[0, 100, 0, 100]), "margins_mm"),
    (CONFIG, lambda c: c["defaults"].update(body_font="missing"), "body_font"),
    (CONFIG, lambda c: c.update(page_presets_mm={"교정판": [210, 148]}), "page_presets_mm"),
    (CONFIG, lambda c: c.update(page_presets_mm={"교정판": [True, 192]}), "page_presets_mm"),
    (CONFIG, lambda c: c.update(space_pct_range=[100, 99]), "space_pct_range"),
    (CONFIG, lambda c: c.update(new_paragraph_layout="unknown"), "new_paragraph_layout"),
    (CONFIG, lambda c: c.update(space_pct_range=[70.001, 130]), "space_pct_range"),
    (CONFIG, lambda c: c.update(heading_per_10000=[True] * 6), "heading_per_10000"),
    (CONFIG, lambda c: c["text_defaults"].update(gloss_per_10000=10000), "gloss_per_10000"),
    (CONFIG, lambda c: c["text_defaults"].update(note_pt=True), "note_pt"),
    (CONFIG, lambda c: c["text_defaults"].update(heading_weight="large"), "heading_weight"),
    (CONFIG, lambda c: c.update(heading_leading_per_10000=0), "heading_leading_per_10000"),
    (CONFIG, lambda c: c["fonts"].pop(), "nanum-gothic"),
    (CONFIG, lambda c: c["fonts"].append(c["fonts"][0]), "nanum-myeongjo"),
    ("runtime.lock", lambda c: c["fonts"]["nanum-gothic"]["regular"].update(path="/fonts/Bad%.ttf"), "regular"),
    ("runtime.lock", lambda c: c["fonts"]["nanum-gothic"]["bold"].update(path="/elsewhere/Gothic.ttf"), "bold"),
    ("runtime.lock", lambda c: c["fonts"]["nanum-gothic"]["bold"].update(path="/usr/share/fonts/truetype/nanum/Bad.ttf/"), "bold"),
])
def test_invalid_install_identifies_setting(install, file, edit, field):
    rewrite(install, file, edit)
    proc = start(install, "from app.style import Style")
    assert proc.returncode != 0
    assert field in proc.stderr.splitlines()[-1]


def test_config_decimals_are_validated_before_rounding(install):
    path = install / CONFIG
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["defaults"]["space_pct"] = ["VALUE", 100]
    path.write_text(json.dumps(cfg).replace('"VALUE"', '69.999999999999999999999999999'), encoding="utf-8")
    proc = start(install, "from app.style import Style")
    assert proc.returncode != 0
    assert "space_pct" in proc.stderr.splitlines()[-1]

def test_expanded_space_policy_still_respects_engine_dimensions(install):
    rewrite(install, CONFIG, lambda c: c.update(space_pct_range=[70, 100000000000]))
    proc = start(install, """
from app.style import Style, apply_style
from app.document import InputError
s = Style()
try:
    apply_style(s, {"space_pct": [100, 100000000000]})
except InputError as e:
    assert e.field == "space_pct"
else:
    raise AssertionError("spacing exceeds a TeX dimension")
""")
    assert proc.returncode == 0, proc.stderr


def test_report_style(tmp_path):
    from app.document import parse_md
    from app.style import Style, apply_style
    from app.typeset import typeset
    from test_reference_typeset import assert_appearance, color, glyphs, matching

    style = apply_style(Style(), {"heading_font": "nanum-myeongjo",
        "text_styles": {"body": {"color": "#222222"}, "heading1": {
            "font": "nanum-gothic", "font_pt": 18, "weight": "bold", "color": "#17365D"},
            "note": {"font": "nanum-gothic", "font_pt": 9, "weight": "bold", "color": "#714126"}},
        "section_rules": [{"path": [], "role": "header", "values": {"visible": True, "left": "기관"}},
                          {"path": [], "role": "footer", "values": {"visible": True, "center": "{현재쪽}"}}]})
    source = "# 능선\n\n봄 바람 *서풍* 산[^바람] \n\n- 달빛\n\n[^바람]: 측정 기록"
    chars = glyphs(typeset(parse_md(source), style, tmp_path))
    ink = lambda code: tuple(int(code[n:n+2], 16) / 255 for n in (0, 2, 4))
    assert_appearance(matching(chars, "능"), 18, "NanumGothicBold", ink("17365D"))
    for word in "봄달":
        assert_appearance(matching(chars, word), 11, "NanumMyeongjo", ink("222222"))
    assert_appearance(matching(chars, "측"), 9, "NanumGothicBold", ink("714126"))
    heading_y = matching(chars, "능")[0].matrix[5]
    header = sorted((c for c in chars if c.matrix[5] > heading_y), key=lambda c: c.x0)
    assert "".join(c.get_text() for c in header) == "기관"
    assert matching(chars, "서")[0].matrix[2] > matching(chars, "봄")[0].matrix[2]
    ref, note, folio = matching(chars, "1")
    assert_appearance([ref], 7.7, "NanumMyeongjo", ink("222222"))
    assert_appearance([note], 6, "NanumGothicBold", ink("714126"))
    assert ref.matrix[5] > matching(chars, "산")[0].matrix[5]
    assert note.matrix[5] > matching(chars, "측")[0].matrix[5]
    assert color(folio) == (0, 0, 0)


def test_scoped_heading_preserves_other_role_attributes(tmp_path):
    from app.document import parse_md
    from app.style import Style, apply_style
    from app.typeset import typeset
    from test_reference_typeset import assert_appearance, matching, glyphs

    style = apply_style(Style(), {"heading_font": "nanum-myeongjo", "text_styles": {
        "heading1": {"font_pt": 19, "weight": "normal", "slant": "italic", "color": "#335577"},
        "body": {"weight": "bold"}}, "section_rules": [
            {"path": ["산"], "role": "heading", "values": {"font_pt": 14, "color": "#ff0000"}}]})
    chars = glyphs(typeset(parse_md("# 달\n\n- 길\n\n# 산\n\n물"), style, tmp_path))
    assert_appearance(matching(chars, "산"), 14, "NanumMyeongjo", (1, 0, 0))
    assert_appearance(matching(chars, "달"), 19, "NanumMyeongjo", (0.2, 1/3, 7/15))
    assert matching(chars, "산")[0].matrix[2] > 0
    for c in ("①", "길", "물"):
        assert matching(chars, c)[0].fontname.endswith("NanumMyeongjoBold")


def test_unused_heading_style_preserves_footnote_geometry(tmp_path):
    from app.document import parse_md
    from app.style import Style, apply_style
    from app.typeset import typeset
    from test_reference_typeset import glyphs

    doc = parse_md("들[^주]끝\n\n[^주]: 밭")
    layouts = []
    for name, patch in [("legacy", {}), ("styled", {"text_styles": {"heading3": {"color": "#112233"}}})]:
        work = tmp_path / name
        work.mkdir()
        chars = glyphs(typeset(doc, apply_style(Style(), patch), work))
        layouts.append([(c.get_text(), c.fontname.split("+")[-1], c.size, c.bbox) for c in chars])
    assert layouts[1] == layouts[0]


def test_footnote_marks_follow_installed_weight_and_slant(tmp_path, monkeypatch):
    from app.document import parse_md
    from app.style import Style, TEXT_DEFAULTS, apply_style
    from app.typeset import typeset
    from test_reference_typeset import glyphs, matching

    monkeypatch.setitem(TEXT_DEFAULTS, "weight", "bold")
    monkeypatch.setitem(TEXT_DEFAULTS, "slant", "italic")
    style = apply_style(Style(), {"section_rules": [
        {"path": [], "role": "body", "values": {"font": "nanum-gothic"}}]})
    chars = glyphs(typeset(parse_md("들[^주]\n\n[^주]: 밭"), style, tmp_path))
    ref, label = matching(chars, "1")
    for mark, letter, face in [(ref, "들", "NanumGothicBold"), (label, "밭", "NanumMyeongjoBold")]:
        adjacent, = matching(chars, letter)
        assert mark.fontname.endswith(face) and adjacent.fontname.endswith(face)
        assert mark.matrix[2] == adjacent.matrix[2] > 0
        assert mark.size < adjacent.size and mark.matrix[5] > adjacent.matrix[5]


@pytest.mark.parametrize("levels", [(1, 2, 3, 4, 5, 6), (2,)])
def test_direct_heading_styles_after_fences(tmp_path, levels):
    from app.document import parse_md
    from app.style import Style, apply_style
    from app.typeset import typeset
    from test_reference_typeset import assert_appearance, color, glyphs, matching

    titles = "솔달별숲꽃잎"
    headings = ["솔\n===", "달\n---", "### 별", "#### 숲", "##### 꽃", "###### 잎"]
    doc = parse_md("\n\n".join("~~~\n표본\n~~~\n" + heading for heading in headings))
    styles = {f"heading{level}": {"font_pt": 12 + level, "color": "#0000FF"} for level in levels}
    chars = glyphs(typeset(doc, apply_style(Style(), {"text_styles": styles}), tmp_path))
    fences = matching(chars, "~")
    assert fences and all(color(c) == (0, 0, 0) for c in fences)
    fence_rows = {c.matrix[5] for c in fences}
    for level, title in enumerate(titles, 1):
        letter, = matching(chars, title)
        if level in levels:
            assert_appearance([letter], 12 + level, "NanumGothicBold", (0, 0, 1))
            assert letter.matrix[5] not in fence_rows
        elif level == 1:
            # A legacy Setext level still shares the closing fence's printed line.
            assert letter.matrix[5] in fence_rows


def test_installed_justified_defaults_drive_new_documents_only(install):
    def edit(config):
        config["defaults"]["ko_tracking_pct"] = 1
        config["justified_spacing"] = {"ko_tracking_bounds_pct": [-2, 4.5], "space_pct": [91, 112]}
    rewrite(install, CONFIG, edit)
    result = start(install, """
from app.style import NEW_DOCUMENT_STYLE, Style, editor_bootstrap
stored = NEW_DOCUMENT_STYLE.to_stored()
assert stored["ko_tracking_per_10000"] == 100 and stored["ko_tracking_bounds_per_10000"] == [-200, 450]
assert stored["space_per_10000"] == [9100, 11200]
assert editor_bootstrap()["new_spacing"] == editor_bootstrap()["justified_spacing"] == {
    "ko_tracking_bounds_pct": ["-2", "4.5"], "space_pct": ["91", "112"]}
assert Style().space_per_10000 == (10000, 10000) and Style().paragraph_layout is None
""")
    assert result.returncode == 0, result.stderr
