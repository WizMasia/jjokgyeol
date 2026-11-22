"""Offline UTF-8 and HTML readers."""
from dataclasses import dataclass
from html.parser import HTMLParser
import re

from app.manuscript_markup import literal_text, protect_block
from .common import ImportResult, LIMITS, styled


def decoded(data):
    text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise ValueError("원고에 널 문자가 있습니다.")
    return text


def plain(data):
    text = protect_block(literal_text(decoded(data)))
    if len(text.encode()) > LIMITS["max_output_bytes"] or not text.strip():
        raise ValueError("빈 원고이거나 변환 원고 크기가 한도를 넘었습니다.")
    return ImportResult(text, [])


@dataclass
class _List:
    kind: str
    first: bool | None = None  # None between items, False after the first paragraph.


class _HTML(HTMLParser):
    def __init__(self, out):
        super().__init__(convert_charrefs=True)
        self.out = out
        self.lists = []
        self.heading = None
        self.runs = []
        self.bold = self.italic = 0
        self.links = []
        self.skip = None

    def append(self, text):
        if text:
            self.runs.append((text, self.bold > 0, self.italic > 0))

    def flush(self, *, close_item=False):
        row = self.getpos()[0]
        text = styled(self.runs, lambda msg: self.out.warn(f"HTML {row}행 · {msg}"), escaped=True).strip()
        self.runs.clear()
        item = self.lists[-1] if self.lists else None
        if item is not None and item.first is not None:
            if text or (close_item and item.first):
                self.out.block(text, item.kind if item.first else "continuation", len(self.lists))
                item.first = False
        elif text:
            self.out.block(text, "heading" if self.heading else "body", self.heading or 1)
        self.heading = None

    def handle_starttag(self, tag, attrs):
        self.out.count()
        if self.skip is not None:
            skip_tag, depth = self.skip
            if tag == skip_tag:
                self.skip = (skip_tag, depth + 1)
            return
        if tag in ("head", "script", "style", "iframe", "object", "svg"):
            self.skip = (tag, 1)
        if tag in ("img", "embed", "iframe", "object", "svg"):
            row, column = self.getpos()
            element = "그림" if tag == "img" else tag
            self.out.warn(f"HTML {row}행 {column + 1}열의 {element} 요소를 생략했습니다.")
        elif tag in ("ul", "ol"):
            self.flush(close_item=True)
            self.lists.append(_List("ordered" if tag == "ol" else "bullet"))
            if len(self.lists) > LIMITS["max_nesting"]:
                raise ValueError("HTML 목록 중첩이 너무 깊습니다.")
        elif tag == "li":
            self.flush(close_item=True)
            if self.lists:
                self.lists[-1].first = True
        elif tag in ("p", "div", "section", "article") or re.fullmatch(r"h[1-6]", tag):
            self.flush()
            if tag.startswith("h"):
                self.heading = int(tag[1])
        elif tag in ("b", "strong"):
            self.bold += 1
        elif tag in ("i", "em"):
            self.italic += 1
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            self.links.append(href)
            if href:
                self.append("[")
        elif tag == "br":
            self.handle_data(" ")

    def handle_endtag(self, tag):
        if self.skip is not None:
            skip_tag, depth = self.skip
            if tag == skip_tag:
                self.skip = None if depth <= 1 else (skip_tag, depth - 1)
            return
        if tag in ("ul", "ol", "li"):
            self.flush(close_item=True)
            if self.lists:
                if tag == "li":
                    self.lists[-1].first = None
                else:
                    self.lists.pop()
        elif tag in ("p", "div", "section", "article") or re.fullmatch(r"h[1-6]", tag):
            self.flush()
        elif tag in ("b", "strong"):
            self.bold = max(0, self.bold - 1)
        elif tag in ("i", "em"):
            self.italic = max(0, self.italic - 1)
        elif tag == "a" and self.links:
            href = self.links.pop()
            if href:
                self.append("](" + href.replace(" ", "%20").replace("(", "%28").replace(")", "%29") + ")")

    def handle_data(self, data):
        if not self.skip:
            text = re.sub(r"[ \t\r\n\f]+", " ", data)
            if self.runs and self.runs[-1][0].endswith(" "):
                text = text.lstrip(" ")
            self.append(literal_text(text))


def html(data, out):
    parser = _HTML(out)
    parser.feed(decoded(data))
    parser.close()
    parser.flush()
