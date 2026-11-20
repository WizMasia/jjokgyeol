"""Geometry of a horizontal typeset page, in PDF points from the bottom left."""
from dataclasses import dataclass
from io import BytesIO

from pdfminer.high_level import extract_pages
from pdfminer.layout import LTChar, LTContainer, LTTextLine


@dataclass(frozen=True)
class Char:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    baseline: float
    advance: float
    font: str
    size: float


@dataclass(frozen=True)
class Line:
    text: str
    chars: tuple[Char, ...]


@dataclass(frozen=True)
class Page:
    width: float
    height: float
    lines: tuple[Line, ...]


def _lines(node):
    if isinstance(node, LTTextLine):
        yield node
    elif isinstance(node, LTContainer):
        for child in node:
            yield from _lines(child)


def read_pages(pdf: bytes) -> list[Page]:
    pages = []
    for page in extract_pages(BytesIO(pdf)):
        lines = []
        for row in sorted(_lines(page), key=lambda row: (-row.y1, row.x0)):
            chars = tuple(
                Char(c.get_text(), *c.bbox, c.matrix[5], c.adv, c.fontname, c.size)
                for c in row if isinstance(c, LTChar)
            )
            if chars:
                lines.append(Line(row.get_text().rstrip("\n"), chars))
        pages.append(Page(page.width, page.height, tuple(lines)))
    return pages
