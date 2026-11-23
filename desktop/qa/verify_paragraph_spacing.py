"""Check installed editing, style export and paragraph gaps measured in a real PDF."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from compare_pdfs import read_pages


def worker_json(worker, command, request):
    result = subprocess.run([worker, command], input=json.dumps(request).encode(),
                            stdout=subprocess.PIPE, timeout=30, check=True)
    return json.loads(result.stdout)


def render_page(runtime, worker, source, style):
    with TemporaryDirectory() as temp:
        folder = Path(temp)
        (folder / "request.json").write_text(json.dumps({"source": source, "style": style}), encoding="utf-8")
        subprocess.run([worker, "--runtime", runtime], cwd=folder, timeout=60, check=True)
        page, = read_pages((folder / "response.pdf").read_bytes())
    return page


def verify(runtime):
    name = "jjokgyeol-worker.exe" if sys.platform == "win32" else "jjokgyeol-worker"
    worker = runtime / "worker" / name
    source = "가을\n\n겨울\n\n봄날\n\n여름"
    plain = worker_json(worker, "--style-export", {})
    report = "2026. 11. 23. 작성자\n\n| 항 | 값 |\n| --- | --- |\n| 갑 | 을 |"
    page = render_page(runtime, worker, report, plain)
    text = ["".join(row.text.split()) for row in page.lines]
    if "2026.11.23.작성자" not in text or any("|" in line for line in text):
        raise ValueError(f"Installed worker did not preserve report date and table: {text}")
    cells = {c.text: c for row in page.lines for c in row.chars if c.text in ("항", "값", "갑", "을")}
    if set(cells) != set("항값갑을"):
        raise ValueError("Missing table cells")
    for header, body in (("항", "갑"), ("값", "을")):
        if abs(cells[header].x0 - cells[body].x0) > 1 or cells[header].baseline <= cells[body].baseline:
            raise ValueError("Table cells do not align under their headers")
    if abs(cells["항"].baseline - cells["값"].baseline) > 1 or cells["값"].x0 - cells["항"].x0 < 20:
        raise ValueError("Expected two distinct table columns")
    print("Verified report date and two-column table in the installed worker")
    default = [line.chars[0].baseline for line in render_page(runtime, worker, source, plain).lines]
    style = worker_json(worker, "--style-export", {"text_styles": {"body": {"space_before_pt": "auto"}}})
    edited = worker_json(worker, "--paragraph-align", {"source": source, "first_line": 2, "last_line": 2,
                                                       "space_before_pt": 0, "space_after_pt": 8.25})
    actual = [line.chars[0].baseline for line in render_page(runtime, worker, edited["source"], style).lines]
    if len(default) != 4 or len(actual) != 4:
        raise ValueError("The four manuscript paragraphs must occupy exactly four lines")
    # Relative to the old 4 TeX pt gap, in PDF points. The last automatic gap uses the rule.
    for i, delta in enumerate((-4, 4.25, 0)):
        shift = (actual[i] - actual[i+1]) - (default[i] - default[i+1])
        if abs(shift - delta * 72 / 72.27) > .06:
            raise ValueError(f"Boundary {i + 1}: expected {delta} TeX pt change, measured {shift:.3f} PDF pt")
    print("Installed worker verified zero, fractional and automatic paragraph gaps")


if __name__ == "__main__":
    verify(Path(sys.argv[1]).resolve(strict=True))
