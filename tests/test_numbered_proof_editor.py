"""Exercise saved numbering options and the displayed/downloaded PDF on a real service."""
import json

from playwright.sync_api import expect

from pdf_metrics import read_pages
from test_article_acceptance import save
from test_duplex_acceptance import reopen_file
from test_list_acceptance import download_proof
from test_reference_acceptance import editor, url


def test_numbering_controls_reopen_and_print(editor, tmp_path):
    source = "- 조사\n  - 현장\n    - 기록\n- 검토\n  - 결과"
    editor.locator("#source").fill(source)
    editor.locator("#style summary").click()
    preset = editor.get_by_label("목록 양식", exact=True)
    side = editor.get_by_label("줄 번호 위치")
    preset.select_option(label="계층 번호형")
    expect(editor.locator("#list-levels fieldset")).to_have_count(9)
    side.select_option(label="오른쪽")
    settings = tmp_path / "번호.json"
    settings.write_bytes(save(editor, "#save-style"))
    assert json.loads(settings.read_bytes())["line_numbers"] == "right"
    preset.select_option(label="법령형")
    side.select_option(label="끔")
    reopen_file(editor, settings)
    expect(side).to_have_value("right")
    expect(preset).to_have_value("outline")
    pages = read_pages(download_proof(editor, tmp_path / "지면.pdf"))
    page, = pages
    chars = [c for line in page.lines for c in line.chars if c.text.strip()]
    right = (148 - 18) * 72 / 25.4
    assert [c.text for c in sorted((c for c in chars if c.x0 > right), key=lambda c: -c.baseline)] == list("12345")
    printed = {}
    for c in chars:
        if c.x0 < right:
            printed.setdefault(round(c.baseline, 3), []).append(c)
    assert ["".join(c.text for c in sorted(row, key=lambda c: c.x0))
            for _, row in sorted(printed.items(), reverse=True)] == [
                "1.조사", "1.1.현장", "1.1.1.기록", "2.검토", "2.1.결과",
            ]
    ninth = editor.get_by_role("group", name="9단계", exact=True)
    ninth.get_by_label("표지 표현식").fill("({계층번호})")
    edited = tmp_path / "개별단계.json"
    edited.write_bytes(save(editor, "#save-style"))
    preset.select_option(label="법령형")
    reopen_file(editor, edited)
    expect(ninth.get_by_label("표지 표현식")).to_have_value("({계층번호})")
    expect(editor.get_by_role("group", name="8단계", exact=True).get_by_label("표지 표현식")).to_have_value("{계층번호}.")
    side.select_option(label="끔")
    assert "line_numbers" not in json.loads(save(editor, "#save-style"))
    assert save(editor, "#save") == source.encode()
