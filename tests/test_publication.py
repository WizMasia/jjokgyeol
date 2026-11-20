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


@pytest.mark.parametrize("role", ["heading1", "note", "gloss", "section"])
@pytest.mark.parametrize("bounds", [(-17000, 0), (0, 17000)])
def test_tracking_endpoints_must_fit_document_and_section_sizes(role, bounds):
    from app.document import InputError
    from app.style import Style, apply_style, from_stored_style

    patch = ({"section_rules": [{"path": ["장"], "role": "body", "values": {"font_pt": 100}}]}
             if role == "section" else {"text_styles": {role: {"font_pt": 100}}})
    style = apply_style(Style(), patch)
    for base, edit in [(style, {}), (Style(), patch)]:
        with pytest.raises(InputError) as exc:
            apply_style(base, {**edit, "ko_tracking_bounds_pct": bounds})
        assert exc.value.field == ("section_rules[0].values.font_pt" if role == "section" else "ko_tracking_bounds_pct")
    with pytest.raises(InputError) as exc:
        from_stored_style(dict(style.to_stored(), ko_tracking_bounds_per_10000=[n * 100 for n in bounds]))
    assert exc.value.field == ("section_rules[0].values.font_sp" if role == "section"
                               else "ko_tracking_bounds_per_10000")


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
    (CONFIG, lambda c: c.update(space_pct_range=[100, 99]), "space_pct_range"),
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
