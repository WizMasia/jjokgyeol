"""Resolve article numbers on a render's private copy of the manuscript."""

from collections import defaultdict
from dataclasses import dataclass
import re

from app.document import InputError
from app.list_marks import format_mark


_NUMBER = re.compile(r"제(0*[1-9][0-9]*)조(의[0-9]+)?(?=$|\s|[（(])")
_PIN = "{번호고정}"


def _leaves(inlines):
    for span in inlines:
        if span["kind"] in ("strong", "emphasis"):
            yield from _leaves(span["children"])
        else:
            yield span


def _replace(leaves, start, end, replacement):
    """Edit a visible text range without flattening surrounding emphasis or notes."""
    offset = 0
    for leaf in leaves:
        # A note occupies one non-text position and cannot be part of a match.
        value = leaf["text"] if leaf["kind"] == "text" else "\0"
        stop = offset + len(value)
        if start < stop and offset < end:
            leaf["text"] = value[:max(0, start - offset)] + replacement + value[end - offset:]
            replacement = ""
        if stop >= end:
            break
        offset = stop


@dataclass
class _Article:
    leaves: list
    match: re.Match
    pinned: bool

    def print_number(self, expr, number):
        try:
            label = format_mark(expr, number)
        except ValueError as error:
            raise InputError({"source": "조 번호를 출력할 수 없습니다."}) from error
        # Keep emphasis on the digits when the expression retains 제…조.
        start, end = self.match.span(1)
        if label == self.match.string[:end + 1]:
            return
        if label.startswith("제") and label.endswith("조"):
            _replace(self.leaves, start, end, label[1:-1])
        else:
            _replace(self.leaves, 0, end + 1, label)


def match_article(text):
    """Return the number match and optional visible-text start of the pin separator."""
    match = _NUMBER.match(text)
    if match is None:
        return None
    title = text.rstrip()
    if title.endswith(_PIN) and title[-len(_PIN) - 1].isspace():
        return match, len(title[:-len(_PIN)].rstrip())
    return match, None


def _article(inlines):
    leaves = list(_leaves(inlines))
    text = "".join(leaf["text"] if leaf["kind"] == "text" else "\0" for leaf in leaves)
    result = match_article(text)
    if result is None:
        return None
    match, pin = result
    if pin is not None:
        _replace(leaves, pin, len(text), "")
    return _Article(leaves, match, pin is not None)


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
