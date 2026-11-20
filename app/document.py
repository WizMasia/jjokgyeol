"""Parse the editor's Markdown subset into data for the typesetter."""

import re
from bisect import bisect_left
from dataclasses import dataclass, field, replace

from app.manuscript_markup import read_escape

from markdown_it import MarkdownIt
from markdown_it.rules_block import fence
from markdown_it.token import Token

from app.annotations import DEFINITION, NoteDefinitions, read_annotation


class InputError(ValueError):
    code = "invalid_input"

    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        self.field, self.message = next(iter(errors.items()))
        super().__init__(self.message)


@dataclass
class Inline:
    kind: str
    text: str | None = None
    id: str | None = None
    children: list["Inline"] = field(default_factory=list)


@dataclass
class Block:
    kind: str
    inlines: list[Inline]
    level: int | None = None


@dataclass(kw_only=True)
class ListItem(Block):
    list_kind: str
    group: int
    parent_group: int | None
    ordinal: int
    first: bool


@dataclass
class _Siblings:
    list_kind: str
    group: int
    parent_group: int | None
    ordinal: int

    def paragraph(self, level: int, first: bool) -> ListItem:
        return ListItem("list_item", [], level, list_kind=self.list_kind, group=self.group,
                        parent_group=self.parent_group, ordinal=self.ordinal, first=first)


@dataclass
class _FenceLayout:
    lines: list[str]
    fenced_lines: set[int]
    # A row maps to None for an ordinary title, False/True for an unpinned/pinned article.
    starts: dict[int, bool | None]
    block_rows: list[int]
    # Fence opening row -> source range of the real top-level list it touches.
    fence_groups: dict[int, list[int]]
    list_fences: frozenset[int] = frozenset()


@dataclass
class Document:
    blocks: list[Block]
    footnotes: dict[str, str]
    fenced_blocks: set[int] = field(default_factory=set)
    _fence_layout: _FenceLayout | None = field(default=None, repr=False)

    endnotes: dict[str, str] = field(default_factory=dict)
    memos: dict[str, str] = field(default_factory=dict)

    def hierarchy_layout(self, selected_items):
        """Find selected lists in the fence-aware view, including items the legacy parser joined to an example."""
        layout = self._fence_layout
        if layout is None or not layout.fence_groups:
            return self
        notes = NoteDefinitions(self.footnotes, self.endnotes, self.memos)

        def parse(fences):
            rows = []
            view = _document(layout.lines, notes, layout.fenced_lines, layout.starts, rows, list_fences=fences)
            view._fence_layout = replace(layout, block_rows=rows, list_fences=fences)
            return view

        fences = frozenset(layout.fence_groups)
        view = parse(fences)
        rows = [view._fence_layout.block_rows[i] for i in selected_items(view)]

        def selected(start, end):
            index = bisect_left(rows, start)
            return index < len(rows) and rows[index] < end

        active = frozenset(row for row, span in layout.fence_groups.items() if selected(*span))
        if not active:
            return self
        return view if active == fences else parse(active)

    def render_layout(self, overrides, *, number_articles, running_ids=()):
        """Keep the printed fence/title join unless this title needs its own heading."""
        layout = self._fence_layout
        if running_ids:
            overrides = [dict(values, running_id=section) for values, section in
                         zip(overrides, running_ids, strict=True)]
        if layout is None or not layout.starts:
            return self, overrides
        # Resolve rules against the full outline first. Body and list rules can
        # follow that outline even when the old heading remains joined in print.
        by_source = {(row, block.kind): values for row, block, values in
                     zip(layout.block_rows, self.blocks, overrides, strict=True) if values}
        split = [row for row, pin in layout.starts.items()
                 if pin or (pin is not None and number_articles)
                 or by_source.get((row, "heading"), {}).keys() - {"running_id"}]
        if len(split) == len(layout.starts):
            return self, overrides
        rows = []
        sections = dict(zip(layout.block_rows, running_ids)) if running_ids else {}
        switches = {row: sections[row] for row in layout.starts.keys() - set(split)} if sections else {}
        notes = NoteDefinitions(self.footnotes, self.endnotes, self.memos)
        view = _document(layout.lines, notes, layout.fenced_lines, split, rows, switches,
                         list_fences=layout.list_fences)
        mapped = []
        for row, block in zip(rows, view.blocks, strict=True):
            values = by_source.get((row, block.kind), {})
            # A joined heading begins in the preceding fenced paragraph.
            mapped.append(dict(values, running_id=sections[row]) if sections else values)
        return view, mapped


_IDEOGRAPHIC_INDENT = re.compile(r"\u3000[\u3000 \t]*\Z")


# Start from plain text so links, HTML, entities and TeX escapes stay literal.
_MD = MarkdownIt("zero", {"html": False}).enable(["heading", "lheading", "list", "emphasis"])
_MD.inline.ruler.before("text", "annotation", read_annotation)
# The legacy renderer displays fenced examples with its original block styles.
# Read fence boundaries separately so those examples never enter article numbering.
_FENCES = MarkdownIt("zero").enable(["fence", "list", "heading", "lheading"])
_MD.inline.ruler.before("text", "manuscript_escape", read_escape)


def _list_fence(state, start, end, silent):
    if start + state.env.get("row_offset", 0) not in state.env.get("list_fences", ()):
        return False
    if not fence(state, start, end, silent):
        return False
    if not silent:
        state.tokens.pop()
        # Print the example with the legacy parser, confined to the actual fence.
        example = state.getLines(start, state.line, state.blkIndent, False)
        tokens = []
        _MD.block.parse(example, _MD, {}, tokens)
        for token in tokens:
            token.level += state.level
            if token.map:
                token.map = [row + start for row in token.map]
        state.tokens.extend(tokens)
    return True


_MD.block.ruler.before("list", "list_fence", _list_fence, {"alt": ["paragraph", "list"]})


def _fence_context(source, notes):
    if "```" not in source and "~~~" not in source:
        return set(), {}, {}

    tokens = []
    _FENCES.block.parse(source, _FENCES, {}, tokens)
    fenced = {line for t in tokens if t.type == "fence" for line in range(*t.map)}
    starts = {}
    fence_groups = {}
    for index, t in enumerate(tokens):
        if t.type in ("ordered_list_open", "bullet_list_open") and t.level == 0:
            group = t.map
            if index and tokens[index - 1].type == "fence":
                fence_groups[tokens[index - 1].map[0]] = group
        elif t.type == "fence" and t.level:
            fence_groups[t.map[0]] = group
        if (t.type == "heading_open" and t.level == 0 and t.markup in ("=", "-")
                and t.map[0] - 1 in fenced):
            inline, = _MD.parseInline(tokens[index + 1].content, {"notes": notes})
            match = heading_title(inline).article()
            starts[t.map[0]] = match[1] is not None if match else None
    return fenced, starts, fence_groups


def _tokens(lines, starts, notes, list_fences=()):
    # A closing fence can run into a Setext title in the legacy zero preset.
    # Parse at each requested title boundary and keep maps in original source rows.
    tokens = []
    start = 0
    for end in [*starts, len(lines)]:
        part = _MD.parse("\n".join(lines[start:end]), {"notes": notes, "list_fences": list_fences,
                                                       "row_offset": start})
        if start:
            for token in part:
                if token.map:
                    token.map = [line + start for line in token.map]
        tokens.extend(part)
        start = end
    return tokens


def _inlines(tokens: list[Token], row=0, switches=None) -> list[Inline]:
    root = []
    stack = [root]
    for token in tokens:
        if token.type == "text":
            if switches and "\n" in token.content:
                # Keep the original printed space and mark the first glyph of
                # the new section, even inside emphasis in a joined heading.
                start = 0
                for newline in re.finditer("\n", token.content):
                    row += 1
                    if row in switches:
                        text = token.content[start:newline.end()].replace("\n", " ")
                        stack[-1].append(Inline("text", text=text))
                        stack[-1].append(Inline("running_section", id=str(switches[row])))
                        start = newline.end()
                if start < len(token.content):
                    stack[-1].append(Inline("text", text=token.content[start:].replace("\n", " ")))
            elif token.content:
                stack[-1].append(Inline("text", text=token.content.replace("\n", " ")))
        elif token.type == "unresolved_note":
            raise InputError({"source": "각주 참조에 해당하는 정의가 없습니다."})
        elif token.type in ("note_ref", "endnote_ref", "memo_ref"):
            stack[-1].append(Inline(token.type, id=token.content))
        elif token.type == "gloss":
            word = Inline("text", text=token.content)
            stack[-1].append(Inline("gloss", text=token.meta["explanation"], children=[word]))
        elif token.type in ("em_open", "strong_open"):
            span = Inline("emphasis" if token.type == "em_open" else "strong")
            stack[-1].append(span)
            stack.append(span.children)
        elif token.type in ("em_close", "strong_close"):
            stack.pop()
    return root


def _manuscript(source: str):
    if not isinstance(source, str):
        raise InputError({"source": "마크다운 원고는 문자열이어야 합니다."})
    if re.search(r"[\x00\ud800-\udfff]", source):
        raise InputError({"source": "원고에 조판할 수 없는 문자가 있습니다."})

    notes = NoteDefinitions()
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for index, line in enumerate(lines):
        match = DEFINITION.fullmatch(line)
        if match:
            name, label, content = match.groups()
            if not notes.add(name, label, content):
                kind = "각주" if label is None and name in notes.footnotes else "주석"
                raise InputError({"source": f"{kind} 정의가 중복되었습니다."})
            lines[index] = ""

    return lines, notes, *_fence_context("\n".join(lines), notes)


def heading_tokens(source: str):
    """Yield top-level Markdown (opening, inline) tokens with original source line maps."""
    lines, notes, fenced_lines, starts, _ = _manuscript(source)
    tokens = _tokens(lines, starts, notes)
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.level == 0 and token.map[0] not in fenced_lines:
            yield token, tokens[index + 1]


def heading_title(inline: Token):
    from app.articles import HeadingTitle

    return HeadingTitle.from_parts((t.type, t.content) for t in inline.children or ())


def heading_text(inline: Token) -> str:
    """Visible title for article matching, with notes interrupting text ranges."""
    return heading_title(inline).text


def parse_md(source: str) -> Document:
    lines, notes, fenced_lines, starts, fence_groups = _manuscript(source)
    rows = [] if starts or fence_groups else None
    doc = _document(lines, notes, fenced_lines, starts, rows)
    if rows is not None:
        doc._fence_layout = _FenceLayout(lines, fenced_lines, starts, rows, fence_groups)
    return doc


def _document(lines, notes, fenced_lines, starts, rows=None, switches=None, *, list_fences=()):
    tokens = _tokens(lines, starts, notes, list_fences)
    blocks = []
    groups: list[_Siblings] = []
    group_id = 0
    fenced_blocks = set()
    fence_parents = set()
    for index, token in enumerate(tokens):
        if token.type in ("ordered_list_open", "bullet_list_open"):
            group_id += 1
            start = token.attrs.get("start", 1)
            groups.append(_Siblings("ordered" if token.tag == "ol" else "bullet", group_id,
                                    groups[-1].group if groups else None, max(1, start) - 1))
        elif token.type in ("ordered_list_close", "bullet_list_close"):
            groups.pop()
        elif token.type == "list_item_open":
            # The block parser skips the tail once the item reaches maxNesting.
            if token.level + 1 >= _MD.options.maxNesting:
                raise InputError({"source": "목록 중첩이 너무 깊습니다."})
            groups[-1].ordinal += 1
            blocks.append(groups[-1].paragraph(len(groups), first=True))
            if token.map[0] in list_fences:
                fence_parents.add(len(blocks) - 1)  # A real list marker can introduce the fence itself.
        elif token.type in ("paragraph_open", "heading_open"):
            if groups:
                if tokens[index - 1].type != "list_item_open":
                    blocks.append(groups[-1].paragraph(len(groups), first=False))
            elif token.type == "heading_open":
                blocks.append(Block("heading", [], int(token.tag[1:])))
            else:
                blocks.append(Block("paragraph", []))
        elif token.type == "inline":
            block = blocks[-1]
            block.inlines = _inlines(token.children or [], token.map[0], switches)
            if tokens[index - 1].type == "paragraph_open":
                # Recover stripped U+3000 from the original line, after any
                # Markdown list markers and indentation consumed by the parser.
                line = lines[token.map[0]]
                if "　" in line:
                    start = line.rfind(token.content.partition("\n")[0])
                    indent = _IDEOGRAPHIC_INDENT.search(line, 0, start) if start >= 0 else None
                    if indent:
                        if block.inlines and block.inlines[0].kind == "text":
                            block.inlines[0].text = indent[0] + block.inlines[0].text
                        else:
                            block.inlines.insert(0, Inline("text", text=indent[0]))
        if rows is not None and len(rows) < len(blocks):
            rows.append(token.map[0])
        if (token.type in ("inline", "list_item_open") and token.map[0] in fenced_lines
                and len(blocks) - 1 not in fence_parents):
            fenced_blocks.add(len(blocks) - 1)
    return Document(blocks, notes.footnotes, fenced_blocks, endnotes=notes.endnotes, memos=notes.memos)
