"""Literal punctuation used by the manuscript reader and import writers."""
import re


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


def unescape_text(text):
    return _ESCAPE.sub(r"\1", text)


def literal_text(text):
    return _INLINE.sub(r"\\\g<0>", text)


def protect_block(text):
    return _BLOCK.sub(lambda m: m[0][:-1] + "\\" + m[0][-1], text)
