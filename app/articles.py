"""Resolve article numbers on a render's private copy of the manuscript."""

from collections import defaultdict
from dataclasses import dataclass
import re
from string import Formatter

from app.document import InputError
from app.list_marks import format_mark


_NUMBER = re.compile(r"제(0*[1-9][0-9]*)조(의[0-9]+)?(?=$|\s|[（(])")
_PIN = "{번호고정}"


@dataclass
class HeadingTitle:
    """Printed notes interrupt an article number, memos have no width, and pins follow glosses."""
    text: str
    first_gloss: int | None
    last_gloss_end: int

    @classmethod
    def from_parts(cls, parts):
        chunks = []
        offset = last_gloss_end = 0
        first_gloss = None
        for kind, value in parts:
            if kind in ("text", "gloss"):
                value = value.replace("\n", " ")
            elif kind in ("note_ref", "endnote_ref", "unresolved_note", "running_section"):
                value = "\0"
            else:
                continue
            if kind == "gloss":
                if first_gloss is None:
                    first_gloss = offset
                last_gloss_end = offset + len(value)
            chunks.append(value)
            offset += len(value)
        return cls("".join(chunks), first_gloss, last_gloss_end)

    def article(self):
        return match_article(self.text, pin_from=self.last_gloss_end)


def _fields(node):
    return node if isinstance(node, dict) else vars(node)


def _leaves(inlines):
    for inline in inlines:
        span = _fields(inline)
        kind = span["kind"]
        if kind in ("strong", "emphasis"):
            yield from _leaves(span["children"])
        elif kind != "memo_ref":
            yield span


def _word(span):
    return _fields(span["children"][0]) if span["kind"] == "gloss" else span


def _leaf_title(leaves):
    return HeadingTitle.from_parts((span["kind"], _word(span).get("text")) for span in leaves)


def section_title(inlines):
    title = _leaf_title(_leaves(inlines))
    match = title.article()
    end = match[1] if match else None
    return title.text[:end].rstrip().replace("\0", "")


def _replace(leaves, start, end, replacement, *, align_digits=False):
    """Edit a visible text range without flattening surrounding emphasis or notes."""
    offset = 0
    for span in leaves:
        leaf = _word(span)
        # A printed note occupies one non-text position and cannot be part of a match.
        value = leaf["text"] if leaf["kind"] == "text" else "\0"
        stop = offset + len(value)
        if max(start, offset) < min(end, stop):
            # Right-align ordinal places across spans when the number changes width.
            take = len(replacement)
            if align_digits and stop < end:
                take = max(0, take - (end - stop))
            leaf["text"] = value[:max(0, start - offset)] + replacement[:take] + value[end - offset:]
            replacement = replacement[take:]
            if span["kind"] == "gloss" and not leaf["text"]:
                # A suppressed part of the label has no printed word to explain.
                span.update(kind="text", text="", children=[])
        if stop >= end:
            break
        offset = stop


@dataclass
class _Article:
    leaves: list
    match: re.Match
    pinned: bool
    glossed_label: bool

    def print_number(self, expr, number):
        try:
            label = format_mark(expr, number)
        except ValueError as error:
            raise InputError({"source": "조 번호를 출력할 수 없습니다."}) from error
        # Keep emphasis on the digits when the expression retains 제…조.
        start, end = self.match.span(1)
        if label == self.match.string[:end + 1]:
            return
        if self.glossed_label:
            # Map 제 / digits / 조 to prefix / first ordinal / suffix on their
            # original leaves. A constant label occupies the ordinal position.
            prefix = suffix = ""
            ordinal = label
            offset = 0
            for literal, field, _, _ in Formatter().parse(expr):
                offset += len(literal)
                if field is not None:
                    ordinal = format_mark("{" + field + "}", number)
                    prefix = label[:offset]
                    suffix = label[offset + len(ordinal):]
                    break
            # Work right to left so the source offsets stay valid.
            _replace(self.leaves, end, end + 1, suffix)
            if ordinal != self.match[1]:
                _replace(self.leaves, start, end, ordinal, align_digits=True)
            _replace(self.leaves, 0, start, prefix)
        elif label.startswith("제") and label.endswith("조"):
            _replace(self.leaves, start, end, label[1:-1])
        else:
            _replace(self.leaves, 0, end + 1, label)


def match_article(text, *, pin_from=0):
    """Return the number match and optional visible-text start of the pin separator."""
    match = _NUMBER.match(text)
    if match is None:
        return None
    title = text.rstrip()
    if title.endswith(_PIN, pin_from) and title[-len(_PIN) - 1].isspace():
        return match, len(title[:-len(_PIN)].rstrip())
    return match, None


def _article(inlines):
    leaves = list(_leaves(inlines))
    title = _leaf_title(leaves)
    result = title.article()
    if result is None:
        return None
    match, pin = result
    if pin is not None:
        _replace(leaves, pin, len(title.text), "")
    glossed_label = title.first_gloss is not None and title.first_gloss <= match.end(1)
    return _Article(leaves, match, pin is not None, glossed_label)


def number_articles(blocks, expr, fenced_blocks=()):
    """Resolve headings, then let the usual list formatter consume corrected ordinals."""
    assigned = defaultdict(list)
    forward_branches = []
    next_number = 1
    waiting_for_list = False
    first_group = None
    offset = 0
    for index, block in enumerate(blocks):
        if index in fenced_blocks:
            continue
        if block["kind"] == "heading":
            article = _article(block["inlines"])
            if article is None or expr is None:
                continue
            block["preserve_spaces"] = True
            try:
                base = int(article.match[1])
            except ValueError as error:
                raise InputError({"source": "조 번호가 너무 길어 처리할 수 없습니다."}) from error
            waiting_for_list = True
            if article.match[2] is None:
                number = base if article.pinned else next_number
                assigned[base].append(number)
                next_number = number + 1
            elif article.pinned:
                number = base
            elif base in assigned:
                number = assigned[base][-1]
            else:
                forward_branches.append((article, base))
                continue
            article.print_number(expr, number)
        elif block["kind"] == "list_item" and block["level"] == 1:
            if waiting_for_list:
                first_group = block["group"]
                offset = block["ordinal"] - 1
                waiting_for_list = False
            if block["group"] == first_group:
                block["ordinal"] -= offset

    for article, base in forward_branches:
        candidates = assigned.get(base, ())
        article.print_number(expr, candidates[0] if len(candidates) == 1 else base)
