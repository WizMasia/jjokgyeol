"""Parse the editor's Markdown subset into data for the typesetter."""

import re
from bisect import bisect_left
from copy import copy
from dataclasses import dataclass, field, replace
from functools import cached_property

from app.manuscript_markup import pair_padded_labels, read_escape

from markdown_it import MarkdownIt
from markdown_it.common.utils import mdTrim
from markdown_it.rules_block import fence, lheading, paragraph, table
from markdown_it.rules_block.table import escapedSplit, getLine
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
    align: str | None = field(default=None, kw_only=True)


@dataclass
class Table(Block):
    rows: list[list[list[Inline]]] = field(default_factory=list)
    alignments: list[str] = field(default_factory=list)


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


ALIGNMENT_MARKERS = {
    "left": "<!-- 쪽결 정렬=왼쪽 -->",
    "center": "<!-- 쪽결 정렬=가운데 -->",
    "right": "<!-- 쪽결 정렬=오른쪽 -->",
}
_MARKER_VALUES = {marker: value for value, marker in ALIGNMENT_MARKERS.items()}


@dataclass(frozen=True)
class ParagraphRange:
    marker_start: int
    start: int
    end: int
    align: str | None


@dataclass
class _SourceLayout:
    lines: list[str]
    fenced_lines: set[int]
    # A row maps to None for an ordinary title, False/True for an unpinned/pinned article.
    starts: dict[int, bool | None]
    aligned: dict[int, ParagraphRange]
    # Fence opening row -> source range of the real top-level list it touches.
    fence_groups: dict[int, list[int]]
    list_fences: frozenset[int] = frozenset()
    block_rows: list[int] = field(default_factory=list)
    printed_ranges: dict[int, tuple[int, int]] = field(default_factory=dict)

    @cached_property
    def prose_roles(self):
        lines = self.lines
        if lines[0].startswith("\ufeff"):
            lines, _ = _manuscript("\n".join(lines), strip_bom=True)
        return {span: role for span, role in _prose_ranges(lines).items() if lines[span[0]] not in _MARKER_VALUES}

    def scope_paragraphs(self, targets):
        if not targets:
            return self
        # A literal indented fence can hide a real title or suggest a split inside it.
        fenced = self.fenced_lines.copy()
        starts = self.starts.copy()
        for target in targets.values():
            fenced.difference_update(range(target.start, target.end))
            if starts:
                for row in range(target.start + 1, target.end):
                    starts.pop(row, None)
        lines = self.lines
        if 0 in targets and lines[0].startswith("\ufeff"):
            lines = [lines[0][1:], *lines[1:]]
        return replace(self, lines=lines, fenced_lines=fenced, starts=starts, aligned=self.aligned | targets)


@dataclass
class Document:
    blocks: list[Block]
    footnotes: dict[str, str]
    fenced_blocks: set[int] = field(default_factory=set)
    _layout: _SourceLayout | None = field(default=None, repr=False, compare=False)

    endnotes: dict[str, str] = field(default_factory=dict)
    memos: dict[str, str] = field(default_factory=dict)

    def alignment_blocks(self) -> set[int]:
        """Indexes whose entire printed block is a directly editable paragraph or heading."""
        layout = self._layout
        if layout is None:  # Documents constructed from blocks have no literal source.
            return {i for i, b in enumerate(self.blocks) if b.kind in ("paragraph", "heading")
                    and i not in self.fenced_blocks}
        result = set()
        for i, span in layout.printed_ranges.items():
            block = self.blocks[i]
            role = "body" if block.kind == "paragraph" else f"heading{block.level}"
            if layout.prose_roles.get(span) == role:
                result.add(i)
        return result

    def alignment_layout(self, roles):
        """Split legacy literal/prose joins only for roles with inherited alignment."""
        layout = self._layout
        if not roles or layout is None:
            return self
        printed = {layout.printed_ranges[i] for i in self.alignment_blocks() - self.fenced_blocks}
        targets = {start: ParagraphRange(start, start, end, None)
                   for (start, end), role in layout.prose_roles.items()
                   if role in roles and (start, end) not in printed and start not in layout.aligned}
        if not targets:
            return self
        return _document(layout.scope_paragraphs(targets), NoteDefinitions(self.footnotes, self.endnotes, self.memos))

    def hierarchy_layout(self, selected_items):
        """Find selected lists in the fence-aware view, including items the legacy parser joined to an example."""
        layout = self._layout
        if layout is None or not layout.fence_groups:
            return self
        notes = NoteDefinitions(self.footnotes, self.endnotes, self.memos)

        def parse(fences):
            return _document(replace(layout, list_fences=fences), notes)

        fences = frozenset(layout.fence_groups)
        view = parse(fences)
        rows = [view._layout.block_rows[i] for i in selected_items(view)]

        def selected(start, end):
            index = bisect_left(rows, start)
            return index < len(rows) and rows[index] < end

        active = frozenset(row for row, span in layout.fence_groups.items() if selected(*span))
        if not active:
            return self
        return view if active == fences else parse(active)

    def render_layout(self, overrides, *, number_articles, running_ids=()):
        """Keep the printed fence/title join unless this title needs its own heading."""
        layout = self._layout
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
        sections = dict(zip(layout.block_rows, running_ids)) if running_ids else {}
        switches = {row: sections[row] for row in layout.starts.keys() - set(split)} if sections else {}
        notes = NoteDefinitions(self.footnotes, self.endnotes, self.memos)
        view = _document(replace(layout, starts={row: layout.starts[row] for row in split}), notes, switches)
        mapped = []
        for row, block in zip(view._layout.block_rows, view.blocks, strict=True):
            values = by_source.get((row, block.kind), {})
            # A joined heading begins in the preceding fenced paragraph.
            mapped.append(dict(values, running_id=sections[row]) if sections else values)
        return view, mapped


_IDEOGRAPHIC_INDENT = re.compile(r"\u3000[\u3000 \t]*\Z")


# Start from plain text so links, HTML, entities and TeX escapes stay literal.
_MD = MarkdownIt("zero", {"html": False}).enable(["heading", "lheading", "list", "emphasis"])
_MD.inline.ruler.before("text", "annotation", read_annotation)
_MD.inline.ruler2.after("emphasis", "padded_labels", pair_padded_labels)
# The legacy renderer displays fenced examples with its original block styles.
# Read fence boundaries separately so those examples never enter article numbering.
_FENCES = MarkdownIt("zero").enable(["fence", "list", "heading", "lheading"])
# The editor must distinguish code and quotations that the legacy renderer prints as prose.
_PARAGRAPHS = MarkdownIt("zero").enable(["code", "fence", "blockquote", "list", "heading", "lheading"])
_MD.inline.ruler.before("text", "manuscript_escape", read_escape)


# A dotted Korean byline can otherwise become three nested ordered lists.
_REPORT_DATE = re.compile(r"[0-9]{4}\.[ \t]*(?:0?[1-9]|1[0-2])\.[ \t]*(?:0?[1-9]|[12][0-9]|3[01])\.?(?=[ \t(]|$)")


def _date_paragraph(state, start, end, silent):
    pos = state.bMarks[start] + state.tShift[start]
    if not _REPORT_DATE.match(state.src, pos, state.eMarks[start]):
        return False
    if silent:
        return state.level > 0 and state.sCount[start] <= state.blkIndent
    return lheading(state, start, end, False) or paragraph(state, start, end, False)


def _table_row_width(state, line):
    cells = escapedSplit(mdTrim(getLine(state, line)))
    if len(cells) == 1:  # Prose, including a line with only escaped pipes.
        return None
    return len(cells) - (cells[0] == "") - (cells[-1] == "")


def _report_table(state, start, end, silent):
    if silent and state.sCount[start] < state.blkIndent:
        # Probe a table that may interrupt the lazy continuation of a nested item.
        state = copy(state)
        state.blkIndent = min(state.listIndent, state.sCount[start])
    row = start + state.env.get("row_offset", 0)
    in_table = state.parentType == "table"
    # The zero preset prints indented code as legacy paragraphs, so its is_code_block is disabled.
    code_indent = state.blkIndent + 4
    if row in state.env.get("fenced_lines", ()) or state.sCount[start] >= code_indent:
        return in_table
    if in_table:
        # markdown-it checks its blockquote terminators before padding or truncating a row.
        width = _table_row_width(state, start)
        return width is None or width > state.env["table_width"]
    if start + 1 >= end or state.sCount[start + 1] >= code_indent or not table(state, start, end, True):
        return False
    width = _table_row_width(state, start)
    if width is None:
        return False
    if silent:
        return True
    state.env["table_width"] = width
    return table(state, start, end, False)


for parser in (_MD, _FENCES, _PARAGRAPHS):
    parser.block.ruler.before("lheading", "report_table", _report_table, {"alt": ["paragraph", "blockquote"]})
    parser.block.ruler.before("list", "report_date", _date_paragraph, {"alt": ["list", "paragraph"]})


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
        _MD.block.parse(example, _MD, {"fenced_lines": range(state.line - start)}, tokens)
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


def _tokens(layout, notes):
    lines, aligned = layout.lines, layout.aligned
    # A closing fence can run into a Setext title in the legacy zero preset.
    # Parse at each requested title boundary and keep maps in original source rows.
    tokens = []
    start = 0
    # The legacy parser may join either edge of a marked block to a literal or list.
    stops = {p.end for p in aligned.values() if p.end < len(lines) and lines[p.end]}
    for end in [*sorted(stops.union(layout.starts, aligned)), len(lines)]:
        part = _MD.parse("\n".join(lines[start:end]), {"notes": notes, "list_fences": layout.list_fences,
                                                       "row_offset": start, "fenced_lines": layout.fenced_lines})
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


def _manuscript(source: str, *, strip_bom=False):
    if not isinstance(source, str):
        raise InputError({"source": "마크다운 원고는 문자열이어야 합니다."})
    if re.search(r"[\x00\ud800-\udfff]", source):
        raise InputError({"source": "원고에 조판할 수 없는 문자가 있습니다."})

    notes = NoteDefinitions()
    if strip_bom:
        source = source.removeprefix("\ufeff")
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for index, line in enumerate(lines):
        match = DEFINITION.fullmatch(line)
        if match:
            name, label, content = match.groups()
            if not notes.add(name, label, content):
                kind = "각주" if label is None and name in notes.footnotes else "주석"
                raise InputError({"source": f"{kind} 정의가 중복되었습니다."})
            lines[index] = ""

    return lines, notes


def _prose_ranges(lines):
    tokens = []
    _PARAGRAPHS.block.parse("\n".join(lines), _PARAGRAPHS, {}, tokens)
    return {tuple(t.map): ("body" if t.tag == "p" else "heading" + t.tag[1:]) for t in tokens
            if t.level == 0 and t.type in ("paragraph_open", "heading_open")}


def _paragraph_ranges(lines):
    """Blank only bound markers in this private parse view. Every source row keeps its index."""
    ranges = _prose_ranges(lines)
    markers = {}
    for start, end in ranges:
        # A marker before a real heading can be the lazy tail of the preceding list or quote.
        row = start
        while row and lines[row - 1] in _MARKER_VALUES:
            row -= 1
        while row < end:
            if lines[row] not in _MARKER_VALUES:
                row += 1
                continue
            first = row
            while row < len(lines) and lines[row] in _MARKER_VALUES:
                row += 1
            # At a fresh boundary only tables need a second line to identify their block kind.
            following = _prose_ranges(lines[row:row + 2])
            if any(start == 0 for start, _ in following):
                markers[row] = (first, lines[first:row])
                lines[first:row] = [""] * (row - first)

    if markers:
        ranges = _prose_ranges(lines)
        # Blanking a tentative marker can reveal an indented continuation of a preceding list.
        unbound = markers.keys() - {start for start, _ in ranges}
        for row in unbound:
            first, originals = markers.pop(row)
            lines[first:row] = originals
        if unbound:
            ranges = _prose_ranges(lines)
    result = []
    for start, end in ranges:
        if lines[start] in _MARKER_VALUES:
            continue  # A literal, unattached marker cannot itself acquire a marker.
        marker_start, originals = markers.get(start, (start, []))
        align = _MARKER_VALUES[originals[-1]] if originals else None
        result.append(ParagraphRange(marker_start, start, end, align))
    return result


def paragraph_range(source, first, last):
    lines, _ = _manuscript(source, strip_bom=True)
    if last >= len(lines):
        raise InputError({"source": "선택한 줄이 원고 범위를 벗어났습니다."})
    return next((p for p in _paragraph_ranges(lines) if p.marker_start <= first <= last < p.end), None)


def _layout_source(source):
    lines, notes = _manuscript(source)
    aligned = {}
    if any(marker in source for marker in ALIGNMENT_MARKERS.values()):
        marked_lines, marked_notes = (_manuscript(source, strip_bom=True) if source.startswith("\ufeff")
                                      else (lines, notes))
        aligned = {p.start: p for p in _paragraph_ranges(marked_lines) if p.align}
        if aligned:
            lines, notes = marked_lines, marked_notes
    fenced, starts, groups = _fence_context("\n".join(lines), notes)
    layout = _SourceLayout(lines, fenced, starts, {}, groups)
    return layout.scope_paragraphs(aligned), notes


def heading_tokens(source: str, aligned_roles=()):
    """Yield top-level Markdown (opening, inline) tokens with original source line maps."""
    layout, notes = _layout_source(source)
    if aligned_roles:
        layout = _document(layout, notes).alignment_layout(aligned_roles)._layout
    tokens = _tokens(layout, notes)
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.level == 0 and token.map[0] not in layout.fenced_lines:
            yield token, tokens[index + 1]


def heading_title(inline: Token):
    from app.articles import HeadingTitle

    return HeadingTitle.from_parts((t.type, t.content) for t in inline.children or ())


def heading_text(inline: Token) -> str:
    """Visible title for article matching, with notes interrupting text ranges."""
    return heading_title(inline).text


def parse_md(source: str) -> Document:
    layout, notes = _layout_source(source)
    return _document(layout, notes)


def _document(layout, notes, switches=None):
    lines, aligned = layout.lines, layout.aligned
    tokens = _tokens(layout, notes)
    rows = []
    blocks = []
    groups: list[_Siblings] = []
    group_id = 0
    fenced_blocks = set()
    fence_parents = set()
    printed_ranges = {}
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
            if token.map[0] in layout.list_fences:
                fence_parents.add(len(blocks) - 1)  # A real list marker can introduce the fence itself.
        elif token.type in ("paragraph_open", "heading_open"):
            if groups:
                if tokens[index - 1].type != "list_item_open":
                    blocks.append(groups[-1].paragraph(len(groups), first=False))
            elif token.type == "heading_open":
                blocks.append(Block("heading", [], int(token.tag[1:])))
            else:
                blocks.append(Block("paragraph", []))
            if not groups:
                printed_ranges[len(blocks) - 1] = tuple(token.map)
                if token.map[0] in aligned:
                    blocks[-1].align = aligned[token.map[0]].align
        elif token.type == "table_open":
            blocks.append(Table("table", []))
        elif token.type == "tr_open":
            blocks[-1].rows.append([])
        elif token.type == "th_open":
            style = token.attrs.get("style", "")
            blocks[-1].alignments.append(style.removeprefix("text-align:") or "left")
        elif token.type == "inline" and tokens[index - 1].type in ("th_open", "td_open"):
            blocks[-1].rows[-1].append(_inlines(token.children or []))
            continue
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
        if len(rows) < len(blocks):
            rows.append(token.map[0])
        if (token.type in ("inline", "list_item_open") and token.map[0] in layout.fenced_lines
                and len(blocks) - 1 not in fence_parents):
            fenced_blocks.add(len(blocks) - 1)
    return Document(blocks, notes.footnotes, fenced_blocks, endnotes=notes.endnotes, memos=notes.memos,
                    _layout=replace(layout, block_rows=rows, printed_ranges=printed_ranges))
