"""Literal punctuation used by the manuscript reader and import writers."""
import re

from markdown_it.token import Token


# Escape only manuscript syntax. TeX commands, raw HTML and entities stay literal.
_ESCAPABLE = frozenset(r"\*_[]#+-.)=`~")
_INLINE = re.compile(r"[\\*_\[\]]")
_BLOCK = re.compile(r"^[ \t]*(?:(?:[0-9]{1,9}[.)]|[-+]|#{1,6})(?=[ \t]|$)|"
                    r"(?:-+|=+)(?=[ \t]*$)|`(?=``)|~(?=~~))", re.M)
_ESCAPE = re.compile(r"\\([" + re.escape("".join(sorted(_ESCAPABLE))) + r"])")


def read_escape(state, silent):
    pos = state.pos
    if state.src[pos] != "\\" or pos + 1 >= state.posMax or state.src[pos + 1] not in _ESCAPABLE:
        return False
    if not silent:
        state.pending += state.src[pos + 1]
    state.pos += 2
    return True


_PADDED_LABEL = re.compile(r"[:：][ \t]+$")


def pair_padded_labels(state):
    """Recover a report label such as **구분: **본문 after normal Markdown pairing."""
    tokens = state.tokens
    stars = {}
    claimed = set()
    for mark in state.delimiters:
        if mark.marker == ord("*") and mark.length >= 2:
            if mark.length == 2 and tokens[mark.token].content == "*":
                stars[mark.token] = mark.open and not mark.close
            else:
                claimed.add(mark.token)
    if len(stars) < 4:
        return

    # Each inline scope has its own opener. Escaped stars have no delimiter entry.
    openers = [None]
    boundary = -1
    for i, token in enumerate(tokens):
        if i in claimed or "\n" in token.content or token.type in ("strong_open", "softbreak", "hardbreak"):
            boundary = i
        if token.nesting == 1:
            openers.append(None)
        elif token.nesting == -1:
            openers.pop()
        elif i in stars and i + 1 in stars:
            start = openers[-1]
            if start is not None and start > boundary and _PADDED_LABEL.search(tokens[i - 1].content):
                tokens[start].content = tokens[i + 1].content = ""
                tokens[start + 1] = Token("strong_open", "strong", 1, markup="**")
                tokens[i] = Token("strong_close", "strong", -1, markup="**")
                openers[-1] = None
            else:
                openers[-1] = i if stars[i] else None


def unescape_text(text):
    return _ESCAPE.sub(r"\1", text)


def literal_text(text):
    return _INLINE.sub(r"\\\g<0>", text)


def protect_block(text):
    return _BLOCK.sub(lambda m: m[0][:-1] + "\\" + m[0][-1], text)
