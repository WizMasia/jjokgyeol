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
    rewrite(install, CONFIG, edit)
    result = start(install, """
from app.document import InputError
from app.publication import engine_policy
from app.style import Style, apply_style, from_stored_style, editor_bootstrap
s = Style()
assert (s.body_sp, s.body_font) == (851968, "nanum-gothic")
assert editor_bootstrap()["defaults"]["body_pt"] == "13"
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
