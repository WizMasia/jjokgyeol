"""Publication list levels in editor units and in saved scaled points."""

from decimal import Decimal
import json

import pytest

from app.document import InputError
from app.style import Style, apply_style, editor_bootstrap, from_stored_style
from test_publication import install, rewrite, start


LEGACY = {
    "page_sp": [27597261, 39158276], "margins_sp": [3729360, 3356424, 3729360, 3356424],
    "body_sp": 720896, "leading_sp": 1114112, "body_font": "nanum-myeongjo",
    "heading_font": "nanum-gothic", "first_indent_sp": 0, "rest_indent_sp": 0,
    "ko_tracking_per_10000": 0, "space_per_10000": [10000, 10000],
}
LEGAL = ["{원숫자}", "{숫자}.", "{한글}.", "({숫자})", "({한글})"]
REPORT = ["ㅁ", "ㅇ", "-"]


def test_legacy_uses_default_without_materializing_list_style():
    old = from_stored_style(LEGACY)
    assert old.to_stored() == Style().to_stored() == LEGACY
    assert "list_levels" not in old.to_editor()
    assert [row.expr for row in old.resolved_list_levels()] == LEGAL
    assert old.to_stored() == LEGACY


def test_presets_are_editable_rows_and_do_not_change_default_fields():
    boot = editor_bootstrap()
    assert boot["default_list_preset"] == "legal"
    assert "list_levels" not in boot["defaults"]
    for name, expressions in [("legal", LEGAL), ("report", REPORT)]:
        rows = boot["list_presets"][name]
        assert [r["expr"] for r in rows] == expressions
        assert all(isinstance(r["indent_mm"], str) and isinstance(r["gap_mm"], str) for r in rows)
        s = apply_style(Style(), {"list_levels": rows})
        assert s.to_editor()["list_levels"] == rows
        assert [r.expr for r in s.resolved_list_levels()] == expressions
        assert "list_levels" in s.to_stored()
    boot["list_presets"]["legal"][0]["expr"] = "편집"
    assert editor_bootstrap()["list_presets"]["legal"][0]["expr"] == "{원숫자}"


def test_list_decimal_rounding_and_expression_literal_round_trip():
    expr = "  {{붙임}}  {숫자}호  "
    rows = [{"expr": expr, "indent_mm": Decimal("0.019378662109375"),
             "gap_mm": "0.0193786621093749999999999999999999999"}]
    s = apply_style(Style(), {"list_levels": rows})
    rows[0]["expr"] = "다른 표지"
    stored = s.to_stored()
    assert stored["list_levels"] == [{"expr": expr, "indent_sp": 3614, "gap_sp": 3613}]
    for _ in range(3):
        s = from_stored_style(json.loads(json.dumps(stored), parse_int=Decimal))
        assert s.to_editor()["list_levels"][0]["expr"] == expr
        assert apply_style(Style(), s.to_editor()).to_stored() == stored
        assert apply_style(s, {"body_pt": 12}).resolved_list_levels() == s.resolved_list_levels()


@pytest.mark.parametrize(("bad", "field"), [
    ([], "list_levels"), (None, "list_levels"),
    ([{"expr": "하나", "indent_mm": 0, "gap_mm": 1}, {"expr": "{빠짐}", "indent_mm": 0, "gap_mm": 0}], "list_levels[1].expr"),
    ([{"expr": "{{}", "indent_mm": 0, "gap_mm": 0}], "list_levels[0].expr"),
    ([{"expr": 2, "indent_mm": 0, "gap_mm": 0}], "list_levels[0].expr"),
    ([{"expr": "-", "indent_mm": "-1e-80", "gap_mm": 0}], "list_levels[0].indent_mm"),
    ([{"expr": "-", "indent_mm": False, "gap_mm": 0}], "list_levels[0].indent_mm"),
    ([{"expr": "-", "indent_mm": 0, "gap_mm": "1e30"}], "list_levels[0].gap_mm"),
    ([{"expr": "-", "indent_mm": 0}], "list_levels[0].gap_mm"),
    ([{"expr": "-", "indent_mm": 0, "gap_mm": 0, "unknown": 1}], "list_levels[0]"),
])
def test_errors_name_stage_and_preserve_original(bad, field):
    s = Style()
    with pytest.raises(InputError) as caught:
        apply_style(s, {"body_pt": 12, "list_levels": bad})
    assert caught.value.field == field
    if "[" in field:
        assert ("2단계" if "[1]" in field else "1단계") in caught.value.message
    assert s.to_stored() == LEGACY


@pytest.mark.parametrize(("key", "value"), [("gap_sp", 1.5), ("indent_sp", "1"), ("gap_sp", -1), ("indent_sp", 2**53)])
def test_stored_distance_is_a_nonnegative_exact_integer(key, value):
    row = {"expr": "{한글}", "indent_sp": 2, "gap_sp": 1, key: value}
    with pytest.raises(InputError) as caught:
        from_stored_style({**LEGACY, "list_levels": [row]})
    assert caught.value.field == f"list_levels[0].{key}"


def test_installed_fallback_and_invalid_preset_are_checked_at_start(tmp_path):
    import shutil
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    install = tmp_path / "install"
    shutil.copytree(root / "app", install / "app", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(root / "runtime.lock", install)
    path = install / "app/config/publication.json"
    config = json.loads(path.read_bytes())
    config["default_list_preset"] = "report"
    path.write_text(json.dumps(config), encoding="utf-8")
    proc = start(install, """
from app.style import Style, from_stored_style
old = Style().to_stored()
assert "list_levels" not in old
assert [r.expr for r in from_stored_style(old).resolved_list_levels()] == ["ㅁ", "ㅇ", "-"]
""")
    assert proc.returncode == 0, proc.stderr
    config["list_presets"]["legal"][1]["expr"] = "{알수없음}"
    path.write_text(json.dumps(config), encoding="utf-8")
    proc = start(install, "from app.style import Style")
    assert proc.returncode != 0
    assert "list_presets.legal[1].expr" in proc.stderr.splitlines()[-1]


def test_expression_error_preserves_formatter_reason_and_stage():
    row = {"expr": "표지\x00", "indent_mm": 0, "gap_mm": 2}
    with pytest.raises(InputError) as error:
        apply_style(Style(), {"list_levels": [row]})
    assert error.value.field == "list_levels[0].expr"
    assert "1단계" in error.value.message and "문자" in error.value.message


def test_article_numbering_is_opt_in_for_legacy_and_list_styles():
    assert editor_bootstrap()["default_article_expr"] == "제{숫자}조"
    assert "article_expr" not in editor_bootstrap()["defaults"]
    for saved in (LEGACY, {**LEGACY, "list_levels": [{"expr": "-", "indent_sp": 2, "gap_sp": 1}]}):
        reopened = from_stored_style(saved)
        assert reopened.article_expr is None
        assert "article_expr" not in reopened.to_editor()
        assert apply_style(Style(), reopened.to_editor()).to_stored() == saved


@pytest.mark.parametrize("expr", ["제{숫자}조", "  <em>{{조}} {한글}/{원숫자}</em>  ", ""])
def test_explicit_article_expression_round_trips_as_literal_text(expr):
    style = apply_style(Style(), {"article_expr": expr})
    assert style.article_expr == expr
    assert apply_style(style, {"body_pt": 12}).article_expr == expr
    assert style.to_stored() == {**LEGACY, "article_expr": expr}
    assert from_stored_style(style.to_stored()).to_editor()["article_expr"] == expr


@pytest.mark.parametrize("expr", [None, False, 1, [], "{미정의}", "제{숫자", "조\n번호", "\ud800"])
def test_invalid_article_expression_is_a_field_error(expr):
    before = Style()
    with pytest.raises(InputError) as edited:
        apply_style(before, {"article_expr": expr, "body_pt": 15})
    with pytest.raises(InputError) as stored:
        from_stored_style({**LEGACY, "article_expr": expr})
    assert edited.value.field == stored.value.field == "article_expr"
    assert edited.value.message == stored.value.message
    assert before.to_stored() == LEGACY


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_article_style_file_http_round_trip(anyio_backend):
    import httpx
    from app.server import app

    expr = "  <em>조 {숫자}</em>  "
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as client:
        saved = await client.post("/api/style/export", json={"article_expr": expr})
        assert saved.status_code == 200 and saved.json() == {**LEGACY, "article_expr": expr}
        opened = await client.post("/api/style/open", json=saved.json())
        assert opened.status_code == 200 and opened.json()["article_expr"] == expr
        assert (await client.post("/api/style/export", json=opened.json())).json() == saved.json()

        bad = {"article_expr": "{잘못}"}
        for path, payload in [
            ("/api/style/open", {**LEGACY, **bad}),
            ("/api/style/export", bad),
            ("/api/render", {"source": "# 제7조", "style": bad}),
        ]:
            error = await client.post(path, json=payload)
            assert error.status_code == 422 and error.json()["field"] == "article_expr"


def test_installed_article_default_reaches_html_as_json_and_rejects_invalid_setting(install):
    path = "app/config/publication.json"
    rewrite(install, path, lambda cfg: cfg.update(default_article_expr="<em>{숫자}</em>"))
    proc = start(install, """
import json
from app.server import editor_html
data = editor_html().split('<script type="application/json" id="editor-settings">')[1].split("</script>")[0]
assert "<em>" not in data
assert json.loads(data)["default_article_expr"] == "<em>{숫자}</em>"
assert "article_expr" not in json.loads(data)["defaults"]
""")
    assert proc.returncode == 0, proc.stderr
    rewrite(install, path, lambda cfg: cfg.update(default_article_expr="{잘못}"))
    proc = start(install, "from app.style import Style")
    assert proc.returncode != 0 and "default_article_expr" in proc.stderr.splitlines()[-1]


def test_prior_publication_policy_loads_with_numbering(tmp_path):
    import shutil
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "app", tmp_path / "app", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(root / "runtime.lock", tmp_path)
    path = tmp_path / "app/config/publication.json"
    policy = json.loads(path.read_bytes())
    policy.pop("line_number_gap_pt", None)
    policy["list_presets"].pop("outline", None)
    policy["default_list_preset"] = "report"
    path.write_text(json.dumps(policy))
    proc = start(tmp_path, """
from app.style import editor_bootstrap
settings = editor_bootstrap()
assert settings['default_list_preset'] == 'report'
assert settings['list_presets']['outline'][0]['expr'] == '{계층번호}.'
""")
    assert proc.returncode == 0, proc.stderr
