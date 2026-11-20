"""The optional gutter selects mirrored margins and consumes real line width."""
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.document import InputError
from app.server import app
from app.style import Style, apply_style, from_stored_style
from test_publication import CONFIG, install, rewrite, start


@pytest.mark.parametrize(("gutter", "expected"), [(None, None), ("0", 0), (Decimal("6"), 1118808)])
def test_optional_gutter_roundtrip(gutter, expected):
    patch = {"margins_mm": [15, 10, 15, 12]}
    if gutter is not None:
        patch["gutter_mm"] = gutter
    style = apply_style(Style(), patch)
    stored = style.to_stored()
    assert style.gutter_sp == expected
    assert stored["margins_sp"] == [2797020, 1864680, 2797020, 2237616]
    assert ("gutter_sp" in stored) == (gutter is not None)
    if gutter is not None:
        assert stored["gutter_sp"] == expected
    opened = from_stored_style(stored)
    editor = opened.to_editor()
    assert editor.get("gutter_mm", "fixed") == ("fixed" if gutter is None else str(gutter))
    assert apply_style(Style(), editor).to_stored() == stored
    assert apply_style(opened, {"body_pt": 13}).gutter_sp == expected


def test_old_style_reexports_without_a_gutter():
    proof = Path(__file__).resolve().parents[1] / "proofs/running-heads/style.json"
    old = json.loads(proof.read_bytes())
    editor = from_stored_style(old).to_editor()
    assert "gutter_mm" not in editor
    assert apply_style(Style(), editor).to_stored() == old


def test_installed_margins_drive_new_fixed_style_and_bootstrap(install):
    rewrite(install, CONFIG, lambda cfg: cfg["defaults"].update(margins_mm=[15, 10, 15, 12]))
    result = start(install, """
from app.style import Style, apply_style, editor_bootstrap
s = Style()
assert s.gutter_sp is None and s.margins_sp == (2797020, 1864680, 2797020, 2237616)
editor = editor_bootstrap()["defaults"]
assert editor["margins_mm"] == ["15", "10", "15", "12"] and "gutter_mm" not in editor
assert apply_style(s, {"gutter_mm": 0}).margins_sp == s.margins_sp
""")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("bad", [None, True, -1, "6", Decimal("1.5"), 2**53])
def test_stored_gutter_requires_a_nonnegative_exact_integer(bad):
    with pytest.raises(InputError) as error:
        from_stored_style(dict(Style().to_stored(), gutter_sp=bad))
    assert error.value.field == "gutter_sp"


@pytest.mark.parametrize(("width", "outer", "inner", "gutter"), [
    (28, 10, 12, "6"),
    # Exact sum exhausts the page even though rounding each margin would leave 1sp.
    (".00002", ".000006", ".000006", ".000008"),
    # Positive exact clearance, but the rounded page and three margins each total 3sp.
    (".0000163", ".0000054", ".0000054", ".0000054"),
    (100, 0, 0, "-1e-100"), (100, 0, 0, None), (100, 0, 0, False),
])
def test_gutter_reduces_available_width(width, outer, inner, gutter):
    with pytest.raises(InputError) as error:
        apply_style(Style(), {"page_mm": [width, 100], "margins_mm": [0, outer, 0, inner],
                              "gutter_mm": gutter})
    assert error.value.field == "gutter_mm"


@pytest.mark.parametrize("key", ["first_indent_mm", "rest_indent_mm"])
def test_body_and_section_indents_use_narrowed_width(key):
    base = {"page_mm": [40, 100], "margins_mm": [0, 10, 0, 12]}
    rule = {"path": ["장"], "role": "body", "values": {key: 12}}
    for values, expected in [({key: 12}, key), ({"section_rules": [rule]}, "section_rules[0].values." + key)]:
        with pytest.raises(InputError) as same_request:
            apply_style(Style(), {**base, **values, "gutter_mm": 6})
        assert same_request.value.field == expected
        original = apply_style(Style(), {**base, **values})
        with pytest.raises(InputError) as later_patch:
            apply_style(original, {"gutter_mm": 6})
        assert later_patch.value.field == expected
        assert "gutter_sp" not in original.to_stored()


@pytest.mark.parametrize("section_rule", [False, True])
def test_last_scaled_point_survives_editor_roundtrip(section_rule):
    # Independently shortened mm strings for these values can consume the last sp.
    stored = dict(Style().to_stored(), page_sp=[16672686, 10000000],
                  margins_sp=[0, 3176064, 0, 4259937], gutter_sp=6626360)
    if section_rule:
        stored["section_rules"] = [{"path": [], "role": "list", "values": {"rest_indent_sp": 2610324}}]
    else:
        stored["rest_indent_sp"] = 2610324
    editor = from_stored_style(stored).to_editor()
    assert apply_style(Style(), editor).to_stored() == stored
    with pytest.raises(InputError) as error:
        from_stored_style(dict(stored, gutter_sp=6626361))
    assert error.value.field == ("section_rules[0].values.rest_indent_sp" if section_rule else "rest_indent_sp")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_style_file_and_render_endpoints_use_the_same_gutter():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://local") as client:
        # The numeric tokens differ beyond binary float precision, across half a scaled point.
        for tail, expected in [("7", 0), ("8", 1)]:
            raw = '{"gutter_mm":0.00000268142550288847377888473778884' + tail + '}'
            exported = await client.post("/api/style/export", content=raw)
            assert exported.status_code == 200, exported.text
            assert exported.json()["gutter_sp"] == expected
            opened = await client.post("/api/style/open", json=exported.json())
            assert opened.status_code == 200
            assert (await client.post("/api/style/export", json=opened.json())).json() == exported.json()
        good = await client.post("/api/render", json={"source": "쪽", "style": {"gutter_mm": 6}})
        assert good.status_code == 200 and good.content.startswith(b"%PDF-")
        for url, payload, field in [
            ("/api/style/open", {**Style().to_stored(), "gutter_sp": None}, "gutter_sp"),
            ("/api/style/export", {"gutter_mm": -1}, "gutter_mm"),
            ("/api/render", {"source": "쪽", "style": {"gutter_mm": 112}}, "gutter_mm"),
        ]:
            response = await client.post(url, json=payload)
            assert response.status_code == 422 and response.json()["field"] == field
