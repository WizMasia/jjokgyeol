"""Match source heading paths to per-block publication overrides."""

from collections import Counter, defaultdict

from app.articles import match_article
from app.document import parse_md
from app.running_heads import RUNNING_ROLES
from app.style import RUNNING_DEFAULTS


def _fields(node):
    return node if isinstance(node, dict) else vars(node)


def _title_parts(inlines):
    for inline in inlines:
        span = _fields(inline)
        if span["kind"] == "text":
            yield span["text"]
        elif span["kind"] in ("note_ref", "endnote_ref", "memo_ref"):
            # A note interrupts an article-number match but adds no title text.
            yield "\0"
        elif span["kind"] in ("strong", "emphasis", "gloss"):
            yield from _title_parts(span["children"])


def _title(inlines):
    title = "".join(_title_parts(inlines))
    article = match_article(title)
    if article and article[1] is not None:
        title = title[:article[1]]
    return title.replace("\0", "")


def _outline(blocks, fenced_blocks):
    stack = []
    path = ()
    for index, block in enumerate(blocks):
        fields = _fields(block)
        kind = fields["kind"]
        if kind == "heading" and index not in fenced_blocks:
            level = fields["level"]
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, _title(fields["inlines"])))
            path = tuple(title for _, title in stack)
            yield path, "heading"
        elif kind == "paragraph":
            yield path, "body"
        elif kind == "list_item":
            yield path, "list"
        else:
            # A heading displayed inside a fenced example never opens a section.
            yield path, None


def _named_headings(outline):
    for index, (path, role) in enumerate(outline):
        if role == "heading" and all(path):
            yield index, path


def targets(source: str) -> list[dict]:
    """Return each path's occurrence count and its first heading's Markdown level."""
    doc = parse_md(source)
    records = {}
    for index, path in _named_headings(_outline(doc.blocks, doc.fenced_blocks)):
        if path in records:
            records[path]["count"] += 1
        else:
            records[path] = {"path": list(path), "count": 1, "level": doc.blocks[index].level}
    return list(records.values())


def _heading_counts(outline):
    return Counter(path for _, path in _named_headings(outline))


def _active_rules(outline, rules):
    counts = _heading_counts(outline)
    by_target = defaultdict(dict)
    for rule in rules:
        if not rule.path or counts[rule.path] == 1:
            by_target[rule.path, rule.role].update(rule.values)
    return by_target


def block_values(blocks, style, *, fenced_blocks=()) -> list[dict]:
    """Resolve overrides against source headings before article numbering changes them."""
    rules = [rule for rule in style.section_rules or () if rule.role not in RUNNING_ROLES]
    if not rules:
        return [{} for _ in blocks]
    outline = list(_outline(blocks, fenced_blocks))
    by_target = _active_rules(outline, rules)

    result = []
    for path, role in outline:
        values = dict(by_target.get(((), role), ()))
        if role == "heading":
            values.update(by_target.get((path, role), ()))
        elif role in ("body", "list"):
            for depth in range(1, len(path) + 1):
                values.update(by_target.get((path[:depth], role), ()))
        result.append(values)
    return result


def running_contexts(blocks, style, *, fenced_blocks=()) -> tuple[list[dict], list[list[int]]]:
    """Return sparse face rules in source order and each block's outer-to-inner scope IDs."""
    scopes = [{"path": [], "header": {}, "footer": {}}]
    rules = [rule for rule in style.section_rules or () if rule.role in RUNNING_ROLES]
    if not rules:
        return scopes, [[1] for _ in blocks]
    outline = list(_outline(blocks, fenced_blocks))
    paths = {rule.path for rule in rules}
    ids = {(): 1}
    for path, count in _heading_counts(outline).items():
        if count == 1 and path in paths:
            scopes.append({"path": list(path), "header": {}, "footer": {}})
            ids[path] = len(scopes)
    for rule in rules:
        if rule.path in ids:
            area = scopes[ids[rule.path] - 1][rule.role]
            area.setdefault(rule.face, {}).update(rule.values)

    chains = []
    for path, _ in outline:
        chain = [1]
        for depth in range(1, len(path) + 1):
            scope_id = ids.get(path[:depth])
            if scope_id is not None:
                chain.append(scope_id)
        chains.append(chain)
    return scopes, chains


def running_values(blocks, style, *, fenced_blocks=()) -> list[dict]:
    """Resolve common header and footer values for each original block."""
    scopes, chains = running_contexts(blocks, style, fenced_blocks=fenced_blocks)
    result = []
    for chain in chains:
        areas = {}
        for role in RUNNING_ROLES:
            values = {}
            for scope_id in chain:
                values.update(scopes[scope_id - 1][role].get("default", {}))
            # Installed defaults take effect only where the author has a rule.
            areas[role] = dict(RUNNING_DEFAULTS, **(values or {"visible": False}))
        result.append(areas)
    return result
