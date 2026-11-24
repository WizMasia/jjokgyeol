"""The role and list spacing contract shared by the editor and the native worker."""
import json

import pytest

from app.document import InputError
from app.style import Style, apply_style, from_editor_style, from_stored_style


LEVEL = {"expr": "-", "indent_mm": 0, "gap_mm": 1}


def test_document_paragraph_gap_round_trips_zero_fraction_and_omission():
    zero = from_editor_style({"paragraph_gap_pt": 0})
    assert zero.to_stored()["paragraph_gap_sp"] == 0
    assert zero.to_editor()["paragraph_gap_pt"] == "0"

    omitted = from_editor_style({})
    assert "paragraph_gap_sp" not in omitted.to_stored()
    assert "paragraph_gap_pt" not in omitted.to_editor()

    fractional = from_editor_style({"paragraph_gap_pt": "6.25"})
    assert fractional.to_stored()["paragraph_gap_sp"] == 409600
    assert from_stored_style(fractional.to_stored()).to_editor()["paragraph_gap_pt"] == "6.25"


@pytest.mark.parametrize("value", [-1, 73, "auto"])
def test_invalid_document_paragraph_gap_reports_its_field(value):
    with pytest.raises(InputError) as exc:
        from_editor_style({"paragraph_gap_pt": value})
    assert exc.value.field == "paragraph_gap_pt"


@pytest.mark.parametrize(("stored", "field", "wrong_field"), [
    (False, "paragraph_gap_pt", "paragraph_gap_sp"),
    (True, "paragraph_gap_sp", "paragraph_gap_pt"),
])
def test_document_paragraph_gap_rejects_the_other_transport_unit(stored, field, wrong_field):
    from app.server import _convert_style_payload

    payload = Style().to_stored() if stored else {}
    payload[wrong_field] = 0
    with pytest.raises(InputError) as exc:
        _convert_style_payload(json.dumps(payload).encode(), export=not stored)
    assert exc.value.field == wrong_field


def test_spacing_round_trips_exactly_and_keeps_each_side_optional():
    titles = {f"heading{i}": {"space_before_pt": "0.00000762939453125"} for i in range(1, 7)}
    style = from_editor_style({
        "text_styles": {**titles, "body": {"space_before_pt": "auto", "space_after_pt": 0}},
        "list_levels": [LEVEL, {**LEVEL, "space_after_pt": "auto"},
                        {**LEVEL, "space_before_pt": 72, "space_after_pt": "1.0000152587890625"}],
    })
    saved = json.loads(json.dumps(style.to_stored()))
    assert saved["text_styles"] == {
        **{role: {"space_before_sp": 1} for role in titles},
        "body": {"space_before_sp": "auto", "space_after_sp": 0},
    }
    row = {"expr": "-", "indent_sp": 0, "gap_sp": 186468}
    assert saved["list_levels"] == [row, {**row, "space_after_sp": "auto"},
                                    {**row, "space_before_sp": 4718592, "space_after_sp": 65537}]
    reopened = from_stored_style(saved)
    editor = reopened.to_editor()
    assert editor["text_styles"]["heading1"] == {"space_before_pt": "0.0000152587890625"}
    assert editor["text_styles"]["body"] == {"space_before_pt": "auto", "space_after_pt": "0"}
    assert editor["list_levels"][2]["space_after_pt"] == "1.0000152587890625"
    assert from_editor_style(editor).to_stored() == saved
    patched = apply_style(reopened, {"text_styles": {"body": {"space_before_pt": 2}}}).to_stored()
    assert patched["text_styles"]["body"] == {"space_before_sp": 131072, "space_after_sp": 0}
    assert patched["list_levels"] == saved["list_levels"]


@pytest.mark.parametrize(("key", "value"), [
    ("space_before_pt", "-0.000001"), ("space_after_pt", "72.00000000000000000000001"),
    ("space_before_pt", None), ("space_after_pt", True), ("space_before_pt", "inherit"),
    ("space_after_pt", "1e"), ("space_before_sp", 0.5), ("space_after_sp", "0"),
    ("space_before_sp", -1), ("space_after_sp", 4718593),
])
@pytest.mark.parametrize("role", ["heading4", "list"])
def test_invalid_spacing_reports_the_direction(role, key, value):
    stored = key.endswith("_sp")
    fields = Style().to_stored() if stored else {}
    if role == "list":
        row = {"expr": "-", "indent_sp": 0, "gap_sp": 0} if stored else LEVEL
        fields["list_levels"] = [{**row, key: value}]
        field = f"list_levels[0].{key}"
    else:
        fields["text_styles"] = {role: {key: value}}
        field = f"text_styles.{role}.{key}"
    with pytest.raises(InputError) as exc:
        (from_stored_style if stored else from_editor_style)(fields)
    assert exc.value.field == field


@pytest.mark.parametrize("role", ["note", "gloss"])
def test_inline_roles_cannot_set_paragraph_spacing(role):
    with pytest.raises(InputError) as exc:
        from_editor_style({"text_styles": {role: {"space_after_pt": 0}}})
    assert exc.value.field == f"text_styles.{role}.space_after_pt"
