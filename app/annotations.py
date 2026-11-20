"""Typed note definitions and the inline markup shared by heading and body parsing."""
import re
from dataclasses import dataclass, field

from markdown_it.rules_inline import StateInline


REFERENCE = re.compile(r"\[\^([^\[\]\s]+)\]")
DEFINITION = re.compile(REFERENCE.pattern + r"(?:\{(미주|메모)\})?:[ \t]*(.*)")
_GLOSS = re.compile(r"\[([^\[\]{}\s]+)\]\{풀이=([^}\r\n]*)(\}?)")


@dataclass
class NoteDefinitions:
    footnotes: dict[str, str] = field(default_factory=dict)
    endnotes: dict[str, str] = field(default_factory=dict)
    memos: dict[str, str] = field(default_factory=dict)

    def kind(self, name):
        if name in self.footnotes:
            return "note_ref"
        if name in self.endnotes:
            return "endnote_ref"
        if name in self.memos:
            return "memo_ref"
        return "unresolved_note"

    def add(self, name, label, text):
        if self.kind(name) != "unresolved_note":
            return False
        entries = {None: self.footnotes, "미주": self.endnotes, "메모": self.memos}
        entries[label][name] = text
        return True


def read_annotation(state: StateInline, silent: bool) -> bool:
    match = REFERENCE.match(state.src, state.pos, state.posMax)
    if match:
        if not silent:
            kind = state.env["notes"].kind(match[1])
            state.push(kind, "", 0).content = match[1]
    else:
        match = _GLOSS.match(state.src, state.pos, state.posMax)
        if not match:
            return False
        if not silent:
            if match[2] and match[3]:
                token = state.push("gloss", "", 0)
                token.content = match[1]
                token.meta["explanation"] = match[2]
            else:
                # A draft explanation is literal through the end of this line.
                state.pending += match[0]
                if not match[3] and match.end() == state.posMax:
                    state.pushPending().meta["unfinished_gloss"] = True
    state.pos = match.end()
    return True
