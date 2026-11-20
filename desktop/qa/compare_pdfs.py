from __future__ import annotations

"""Compare native and web proof PDFs. Run from the repository root."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.pdf_metrics import read_pages


def compare(left: bytes, right: bytes) -> str | None:
    try:
        a, b = read_pages(left), read_pages(right)
    except Exception as exc:
        return f"malformed PDF: {exc}"
    if len(a) != len(b):
        return f"page count: {len(a)} != {len(b)} (first differing page {min(len(a), len(b)) + 1})"
    for p, (x, y) in enumerate(zip(a, b), 1):
        if len(x.lines) != len(y.lines):
            return f"page {p}, line {min(len(x.lines), len(y.lines)) + 1}: line count {len(x.lines)} != {len(y.lines)}"
        for n, (lx, ly) in enumerate(zip(x.lines, y.lines), 1):
            if lx.text != ly.text:
                return f"page {p}, line {n}, text: {lx.text!r} != {ly.text!r}"
            for c, (cx, cy) in enumerate(zip(lx.chars, ly.chars), 1):
                if cx.font.split("+")[-1] != cy.font.split("+")[-1]:
                    return f"page {p}, line {n}, char {c}, font: {cx.font!r} != {cy.font!r}"
                for metric in ("x0", "y0", "x1", "y1", "baseline", "advance", "size"):
                    if abs(getattr(cx, metric) - getattr(cy, metric)) > 0.05:
                        return f"page {p}, line {n}, char {c}, {metric}: {getattr(cx, metric):.3f} != {getattr(cy, metric):.3f} pt"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native_pdf", type=Path)
    parser.add_argument("web_pdf", type=Path)
    args = parser.parse_args()
    try:
        difference = compare(args.native_pdf.read_bytes(), args.web_pdf.read_bytes())
    except OSError as exc:
        parser.error(str(exc))
    if difference:
        print(difference, file=sys.stderr)
        return 1
    print("PDFs match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
