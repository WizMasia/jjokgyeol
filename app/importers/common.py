"""Shared Markdown output and per-document resource limits."""
from dataclasses import dataclass
from itertools import groupby
import re

from markdown_it import MarkdownIt
from markdown_it.common.utils import isMdAsciiPunct, isPunctChar, isWhiteSpace

from app.configuration import CONFIG_DIR, read_config
from app.manuscript_markup import literal_text, protect_block, read_escape, unescape_text


LIMITS = read_config(CONFIG_DIR / "imports.json")


@dataclass
class ImportResult:
    markdown: str
    warnings: list[str]


_INLINE = MarkdownIt("zero").enable("emphasis")
_INLINE.inline.ruler.before("text", "manuscript_escape", read_escape)


def _faces_match(markdown, expected):
    bold = italic = 0
    actual = []
    for token in _INLINE.parseInline(markdown)[0].children:
        if token.type == "text" and token.content:
            actual.append((token.content, bool(bold), bool(italic)))
        elif token.tag == "strong":
            bold += token.nesting
        elif token.tag == "em":
            italic += token.nesting
    return list(_joined(actual)) == list(_joined(expected))


def _joined(runs):
    for face, group in groupby((r for r in runs if r[0]), key=lambda r: r[1:]):
        yield ("".join(run[0] for run in group), *face)


def _context(char):
    if not char or isWhiteSpace(ord(char)):
        return " "
    return "." if isMdAsciiPunct(ord(char)) or isPunctChar(char) else "a"


def _face_group(group, before, after, warn):
    # Markdown flanking depends on the neighboring character classes.
    left, right = _context(before), _context(after)
    expected = [(left, False, False)]
    pieces = []
    for i, (text, bold, italic) in enumerate(group):
        core = text.strip()
        if not core:
            pieces.append(text)
            expected.append((text, False, False))
            continue
        start = len(text) - len(text.lstrip())
        end = len(text.rstrip())
        opening = closing = "*" * (2 * bold + italic)
        if bold and italic and (i or left != "a") and (i + 1 < len(group) or right != "a"):
            opening, closing = "_**", "**_"
        pieces.extend((text[:start], opening, core, closing, text[end:]))
        expected.extend(((text[:start], False, False), (unescape_text(core), bold, italic),
                         (text[end:], False, False)))
    candidate = "".join(pieces)
    expected.append((right, False, False))
    if _faces_match(left + candidate + right, expected):
        return candidate
    warn("인접 강조의 일부를 마크다운으로 표현할 수 없어 일반 글자로 옮겼습니다.")
    # Intraword punctuation cannot open/close emphasis. Release just that edge
    # and retry with its punctuation as the neighbor of the remaining face.
    prefix = suffix = ""
    if left == "a":
        text, bold, italic = group[0]
        end = 0
        while end < len(text) and _context(text[end]) == ".":
            end += 1
        prefix = text[:end]
        group[0] = (text[end:], bold, italic)
    if right == "a":
        text, bold, italic = group[-1]
        start = len(text)
        while start and _context(text[start - 1]) == ".":
            start -= 1
        suffix = text[start:]
        group[-1] = (text[:start], bold, italic)
    if prefix or suffix:
        remaining = [r for r in group if r[0]]
        middle = _face_group(remaining, prefix[-1:] or before, suffix[:1] or after, warn) if remaining else ""
        return prefix + middle + suffix
    return "".join(run[0] for run in group)


def styled(runs, warn, *, escaped=False):
    groups = [(text if escaped else literal_text(text), bold, italic)
              for text, bold, italic in _joined(runs)]
    pieces = []
    index = 0
    while index < len(groups):
        text, bold, italic = groups[index]
        if not (bold or italic):
            pieces.append(text)
            index += 1
            continue
        end = index + 1
        while end < len(groups) and any(groups[end][1:]):
            end += 1
        before = groups[index - 1][0][-1:] if index else ""
        after = groups[end][0][:1] if end < len(groups) else ""
        pieces.append(_face_group(groups[index:end], before, after, warn))
        index = end
    return "".join(pieces)


class Manuscript:
    def __init__(self):
        self.parts = []
        self.warnings = {}
        self.size = 0
        self.list_path = []  # Source depths and Markdown marker widths of actual ancestors.
        self.records = 0

    def count(self):
        self.records += 1
        if self.records > LIMITS["max_records"]:
            raise ValueError("원고의 구조 요소 수가 한도를 넘었습니다.")

    def warn(self, message):
        self.warnings[message] = None

    def block(self, text, kind="body", depth=1, *, location=""):
        self.count()
        if type(depth) is not int or depth < 1:
            raise ValueError("문서 구조의 단계 값이 올바르지 않습니다.")
        text = text.strip()
        list_item = kind in ("ordered", "bullet")
        if not text and not list_item:
            return
        text = protect_block(text)
        sep = "\n\n" if self.parts else ""
        if list_item:
            if self.list_path:
                sep = "\n"
            while self.list_path and self.list_path[-1][0] >= depth:
                self.list_path.pop()
            actual = len(self.list_path) + 1
            if actual > 9:
                raise ValueError("편집기에서 열 수 있는 목록 중첩 한도를 넘었습니다.")
            if actual != depth:
                self.warn(f"{location} 목록의 {depth}단계를 기존 항목 아래 {actual}단계로 옮겼습니다.")
            marker = "1. " if kind == "ordered" else "- "
            text = " " * sum(width for _, width in self.list_path) + marker + text
            self.list_path.append((depth, len(marker)))
        elif kind == "continuation":
            while self.list_path and self.list_path[-1][0] > depth:
                self.list_path.pop()
            text = " " * sum(width for _, width in self.list_path) + text
        else:
            self.list_path.clear()
            if kind == "heading":
                if depth > 6:
                    self.warn(f"{location} 제목의 {depth}단계를 마크다운 6단계로 옮겼습니다.")
                text = re.sub(r"(^|\s)(#+)$", r"\1\\\2", text)
                text = "#" * min(depth, 6) + " " + text
        self.size += len(text.encode()) + len(sep)
        if self.size > LIMITS["max_output_bytes"]:
            raise ValueError("변환 원고 크기가 한도를 넘었습니다.")
        self.parts.extend((sep, text))

    def finish(self):
        if not self.parts:
            raise ValueError("가져올 본문이 없습니다.")
        return ImportResult("".join(self.parts), list(self.warnings))
