"""Offline UTF-8 and HTML readers."""
from html.parser import HTMLParser
import re

from .common import ImportResult, LIMITS, Manuscript


def decoded(data):
    text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise ValueError("원고에 널 문자가 있습니다.")
    return text


def plain(data):
    text = decoded(data)
    if len(text.encode()) > LIMITS["max_output_bytes"] or not text.strip():
        raise ValueError("빈 원고이거나 변환 원고 크기가 한도를 넘었습니다.")
    return ImportResult(text, [])


class _HTML(HTMLParser):
    def __init__(self, out):
        super().__init__(convert_charrefs=True)
        self.out = out
        self.lists = []
        self.kind = "body"
        self.level = 1
        self.parts = []
        self.links = []
        self.skip = None

    def flush(self):
        self.out.block(re.sub(r"[ \t\r\n\f]+", " ", "".join(self.parts)), self.kind, self.level)
        self.parts.clear()
        self.kind = "body"

    def handle_starttag(self, tag, attrs):
        self.out.count()
        if self.skip:
            return
        if tag in ("head", "script", "style", "iframe", "object", "svg"):
            self.skip = tag
            if tag in ("iframe", "object", "svg"):
                self.out.warn(f"HTML {tag} 요소를 생략했습니다.")
        elif tag in ("img", "embed"):
            self.out.warn("HTML 그림·임베드 요소를 생략했습니다.")
        elif tag in ("ul", "ol"):
            self.flush()
            self.lists.append("ordered" if tag == "ol" else "bullet")
            if len(self.lists) > LIMITS["max_nesting"]:
                raise ValueError("HTML 목록 중첩이 너무 깊습니다.")
        elif tag in ("li", "p", "div", "section", "article") or re.fullmatch(r"h[1-6]", tag):
            if tag == "p" and self.kind in ("ordered", "bullet") and not "".join(self.parts).strip():
                return
            self.flush()
            if tag == "li":
                self.kind = self.lists[-1] if self.lists else "bullet"
                self.level = max(1, len(self.lists))
            elif tag.startswith("h"):
                self.kind, self.level = "heading", int(tag[1])
        elif tag in ("b", "strong", "i", "em"):
            self.parts.append("**" if tag in ("b", "strong") else "*")
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            self.links.append(href)
            if href:
                self.parts.append("[")
        elif tag == "br":
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if self.skip:
            if self.skip == tag:
                self.skip = None
            return
        if tag in ("ul", "ol"):
            self.flush()
            if self.lists:
                self.lists.pop()
        elif tag in ("p", "li", "div", "section", "article") or re.fullmatch(r"h[1-6]", tag):
            self.flush()
        elif tag in ("b", "strong", "i", "em"):
            self.parts.append("**" if tag in ("b", "strong") else "*")
        elif tag == "a" and self.links:
            href = self.links.pop()
            if href:
                self.parts.append("](" + href.replace(" ", "%20").replace("(", "%28").replace(")", "%29") + ")")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html(data, out):
    parser = _HTML(out)
    parser.feed(decoded(data))
    parser.close()
    parser.flush()
