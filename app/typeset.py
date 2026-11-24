"""Run the fixed book template in a fresh directory owned by one render job."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from app.articles import number_articles
from app.document import InputError
from app.document_fonts import prepare_fonts
from app.list_marks import format_mark, uses_hierarchy
from app.publication import FONT_DIR, LINE_NUMBER_GAP_PT, engine_policy
from app.reference_rules import block_values, running_contexts
from app.running_heads import RUNNING_ROLES, SLOTS, parse_running_text
from app.security import worker_env
from app.style import RUNNING_DEFAULTS, SP_PER_PT, TEXT_DEFAULTS, leading_sp as validate_leading_sp, paragraph_spacing_sp

if TYPE_CHECKING:
    from app.document import Document
    from app.style import Style

TEMPLATES = Path(__file__).resolve().with_name("tex")


def _tex_path(path):
    value = path.as_posix()
    if value.startswith('//?/UNC/'):
        return '//' + value[8:]
    if value.startswith('//?/'):
        return value[4:]
    return value


def typeset(doc: Document, style: Style, workdir: Path, *, collect_candidates: bool = False,
            paragraph_choices: dict[int, str] | None = None,
            spacing_choices: dict[str, int] | None = None, collect_spacing: bool = False,
            leading_choices: dict[str, int] | None = None, collect_leading: bool = False,
            engine: Path | None = None, font_dir: Path | None = None,
            max_pdf_bytes: int | None = None) -> bytes:
    """Optionally collect paragraph-candidates.jsonl or replay its per-block IDs."""
    return _render(doc, style, workdir, None, collect_candidates, paragraph_choices, engine, font_dir,
                   max_pdf_bytes, spacing_choices, collect_spacing, leading_choices, collect_leading)


def _running_fields(values):
    return {key: parse_running_text(value) if key in SLOTS else value for key, value in values.items()}


def _running_sections(doc, style):
    if not any(row.role in RUNNING_ROLES for row in style.section_rules or ()):
        return None, []
    scopes, chains = running_contexts(doc.blocks, style, fenced_blocks=doc.fenced_blocks)
    if not any(scope[role] for scope in scopes for role in RUNNING_ROLES):
        return None, []

    # Each path has a unique deepest ID. Store its ancestry once and attach
    # only that ID to manuscript lines, even when sibling rules look identical.
    ancestry = {1: [1]}
    for chain in chains:
        ancestry[chain[-1]] = chain
    settings = []
    for i, scope in enumerate(scopes, 1):
        faces = {role: {face: _running_fields(values) for face, values in scope[role].items()}
                 for role in RUNNING_ROLES}
        settings.append({"chain": ancestry[i], **faces})
    return {"defaults": _running_fields(dict(RUNNING_DEFAULTS)), "scopes": settings}, [c[-1] for c in chains]


def _role_styles(style):
    """Omit attributes already supplied by the template's ordinary paragraph and heading."""
    explicit = dict(style.text_styles or ())
    styles = {}
    for role in ("body", *(f"heading{level}" for level in range(1, 7))):
        heading = role != "body"
        weight = TEXT_DEFAULTS["heading_weight" if heading else "weight"]
        base = {}
        if weight != ("bold" if heading else "normal"):
            base["weight"] = weight
        if TEXT_DEFAULTS["slant"] != "normal":
            base["slant"] = TEXT_DEFAULTS["slant"]
        if TEXT_DEFAULTS["color"].upper() != "#000000":
            base["color"] = TEXT_DEFAULTS["color"]
        styles[role] = base | dict(explicit.get(role, ()))
    return styles


def _omit_memos(spans):
    visible = []
    for span in spans:
        if span["kind"] == "memo_ref":
            continue
        if span["children"]:
            span["children"] = _omit_memos(span["children"])
            if not span["children"]:
                continue
        visible.append(span)
    return visible


def _styled_list_items(doc, style, levels, spaced_levels):
    overrides = block_values(doc.blocks, style, fenced_blocks=doc.fenced_blocks)
    for i, (block, values) in enumerate(zip(doc.blocks, overrides, strict=True)):
        if block.kind == "list_item" and block.first and i not in doc.fenced_blocks:
            row = levels[min(block.level, len(levels)) - 1]
            if (min(block.level, len(levels)) in spaced_levels
                    or uses_hierarchy(values.get(f"levels.{block.level}.expr", row.expr))):
                yield i


_SPACE_FIELDS = ("space_before_sp", "space_after_sp")


def _spacing_boundaries(blocks, rows, style):
    boundaries = {}
    previous = None
    for index, block in enumerate(blocks):
        before, after = block.pop("spacing", (None, None))
        if block["kind"] in ("paragraph", "heading") and block.get("overrides", {}).get("frame") != "box" and not any(
            span["kind"] != "text" or span["text"].strip(" ") for span in block["inlines"]
        ):
            continue  # A paragraph containing only hidden memos has no printed boundary.
        if previous is not None:
            left, left_after = previous
            sides = [side for side in (left_after, before) if side is not None]
            fixed = [side for side in sides if type(side[0]) is int]
            key = str(index + 1)
            auto = bool(sides and not fixed)
            gap, source = max(fixed, key=lambda side: (side[0], side[1] == "manual")) if fixed else (
                None, "rule" if auto else "default")
            boundaries[key] = dict(id=key, before_line=rows[left], after_line=rows[index],
                                   source=source, auto=auto, left_kind=blocks[left]["kind"],
                                   right_kind=block["kind"], left_level=blocks[left]["level"] or 0,
                                   right_level=block["level"] or 0, body_sp=style.body_sp)
            if gap is not None:
                boundaries[key]["gap_sp"] = gap
        previous = index, after
    return boundaries


def _render(doc, style, workdir, candidate, collect_candidates=False, paragraph_choices=None, engine=None,
            font_dir=None, max_pdf_bytes=None, spacing_choices=None, collect_spacing=False,
            leading_choices=None, collect_leading=False):
    if any(workdir.iterdir()):
        raise ValueError("조판에는 빈 작업 폴더가 필요합니다.")
    role_styles = _role_styles(style)
    aligned_roles = {role for role, fields in role_styles.items() if "align" in fields}
    role_spacing = {role: {k: fields.pop(k) for k in _SPACE_FIELDS if k in fields}
                    for role, fields in role_styles.items()}
    prose_roles = aligned_roles | {role for role, sides in role_spacing.items() if sides}
    doc = doc.alignment_layout(prose_roles)
    if leading_choices is not None:
        eligible_leading = {str(i + 1) for i, block in enumerate(doc.blocks) if block.leading_sp == "auto"}
        if not isinstance(leading_choices, dict) or leading_choices.keys() - eligible_leading:
            raise InputError({"leading": "선택한 자동 줄 간격 문단을 확인하세요."})
        try:
            leading_choices = {key: validate_leading_sp(value, stored=True)
                               for key, value in leading_choices.items()}
        except ValueError:
            raise InputError({"leading": "선택한 줄 간격 값을 확인하세요."}) from None
    levels = style.resolved_list_levels()
    spaced_levels = {i for i, row in enumerate(levels, 1)
                     if row.space_before_sp is not None or row.space_after_sp is not None}
    expressions = [row.expr for row in levels]
    expressions.extend(value for rule in style.section_rules or () if rule.role == "list"
                       for key, value in rule.values if key.endswith(".expr"))
    if spaced_levels or any(map(uses_hierarchy, expressions)):
        doc = doc.hierarchy_layout(lambda view: _styled_list_items(view, style, levels, spaced_levels))
    eligible = doc.alignment_blocks() if prose_roles else ()
    overrides = block_values(doc.blocks, style, fenced_blocks=doc.fenced_blocks)
    for index, block in enumerate(doc.blocks):
        role = f"heading{block.level}" if block.kind == "heading" else "body"
        base = role_styles[role]
        if block.kind == "list_item":
            level = levels[min(block.level, len(levels)) - 1]
            base = base | {k: v for k, v in (("font", level.font), ("font_sp", level.font_sp),
                                              ("weight", level.weight)) if v is not None}
        values = overrides[index] = base | overrides[index]
        if index in eligible and role_spacing[role] or (
            index not in doc.fenced_blocks and block.kind == "list_item" and min(block.level, len(levels)) in spaced_levels
        ) or block.space_before_sp is not None or block.space_after_sp is not None:
            inherited = vars(level) if block.kind == "list_item" else role_spacing[role]
            sides = []
            for field in _SPACE_FIELDS:
                value = getattr(block, field)
                sides.append((value, "manual") if value is not None else
                             (inherited[field], "style") if inherited.get(field) is not None else None)
            values["spacing"] = sides
        leading = block.leading_sp
        if leading == "auto":
            leading = (leading_choices or {}).get(str(index + 1))
        if type(leading) is int:
            values["leading_sp"] = leading
        if direct := getattr(block, "align", None):
            values["align"] = direct
        elif index not in eligible:
            values.pop("align", None)
    settings, ids = _running_sections(doc, style)
    doc, overrides = doc.render_layout(overrides, number_articles=style.article_expr is not None, running_ids=ids)
    blocks = [asdict(block) for block in doc.blocks]
    for block in blocks:
        if block["leading_sp"] is None:
            block.pop("leading_sp")
    number_articles(blocks, style.article_expr, doc.fenced_blocks)
    box_ids = {}
    ordinals = []
    has_spacing = False
    for block, values in zip(blocks, overrides, strict=True):
        block.pop("align", None)  # The resolved value travels in overrides, including through fence layout.
        for field in _SPACE_FIELDS:
            block.pop(field, None)
        if "spacing" in values:
            block["spacing"] = values.pop("spacing")
            has_spacing = True
        if "running_id" in values:
            block["running_id"] = values.pop("running_id")
        if values:
            block["overrides"] = {k: v for k, v in values.items() if not k.startswith("levels.")}
        if block["kind"] != "list_item":
            continue
        depth = block["level"]
        index = min(depth, len(levels)) - 1
        row = levels[index]
        prefix = f"levels.{depth}."
        block["indent_sp"] = values.get(prefix + "indent_sp", row.indent_sp)
        block["gap_sp"] = values.get(prefix + "gap_sp", row.gap_sp)
        if row.indent_origin:
            block["indent_origin"] = row.indent_origin
        if block["first"]:
            ordinals[depth - 1:] = [block["ordinal"]]
            try:
                mark = format_mark(values.get(prefix + "expr", row.expr), block["ordinal"],
                                   parents=tuple(ordinals[:-1]))
            except ValueError as error:
                raise InputError({f"list_levels[{index}].expr": f"{index + 1}단계 표지를 만들 수 없습니다."}) from error
            if row.mark_weight:
                block["mark_weight"] = row.mark_weight
            key = (mark, values.get("font", style.body_font), values.get("font_sp", style.body_sp),
                   values.get("color", TEXT_DEFAULTS["color"]), row.mark_weight or values.get("weight"),
                   values.get("slant"))
            block["mark"] = mark
            block["mark_id"] = box_ids.setdefault(key, len(box_ids) + 1)
    if doc.memos:
        printable = False
        for block in blocks:
            if block["kind"] == "table":
                block["rows"] = [[_omit_memos(cell) for cell in row] for row in block["rows"]]
                printable = True
                continue
            spans = _omit_memos(block["inlines"])
            block["inlines"] = spans
            # Inter-reference spaces do not start a TeX paragraph. An empty list item has a label.
            if not printable:
                printable = block["kind"] == "list_item" or any(
                    span["kind"] != "text" or span["text"].strip(" ") for span in spans
                )
        if not printable:
            blocks = []
    book_style = style.to_stored()
    # Block overrides and running scopes already contain the rules used by TeX.
    book_style.pop("section_rules", None)
    data = {"blocks": blocks,
            "footnotes": dict(doc.footnotes), "style": book_style,
            "policy": engine_policy(), "candidate": candidate}
    if style.paragraph_gap_sp is not None:
        data["policy"]["paragraph_spacing"]["default_pt"] = style.paragraph_gap_sp / SP_PER_PT
    installed_dir = font_dir if font_dir is not None else Path(os.environ.get("JJOKGYEOL_FONT_DIR", FONT_DIR))
    has_document_files = prepare_fonts(style, data["policy"], workdir / "fonts", installed_dir,
                                       copy_installed=font_dir is not None)
    if style.line_numbers:
        data["policy"]["line_number_gap_pt"] = LINE_NUMBER_GAP_PT
    data["text_defaults"] = TEXT_DEFAULTS
    if doc.endnotes:
        data["endnotes"] = doc.endnotes
    if settings:
        data["running_sections"] = settings
    if collect_candidates or paragraph_choices is not None:
        snapshot = json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        data["paragraphs"] = {"snapshot": hashlib.sha256(snapshot).hexdigest(),
                              "collect": collect_candidates, "choices": paragraph_choices or {}}
    if has_spacing or collect_spacing:
        rows = doc._layout.block_rows if doc._layout else range(len(blocks))
        boundaries = _spacing_boundaries(blocks, rows, style)
        for key, gap in (spacing_choices or {}).items():
            boundary = boundaries.get(key)
            if boundary and boundary["auto"] and type(gap) is int:
                boundary.update(gap_sp=paragraph_spacing_sp(gap, stored=True), source="ai")
        if boundaries:
            data["spacing"] = boundaries
    if collect_leading or leading_choices is not None:
        line_rows = doc._layout.block_rows if doc._layout else range(len(blocks))
        data["leading"] = {"collect": True, "choices": leading_choices or {},
                            "lines": {str(i + 1): line for i, line in enumerate(line_rows)}}
    if "spacing" not in data:
        (workdir / "spacing.json").write_text("[]", encoding="utf-8")
    (workdir / "input.json").write_text(
        json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    (workdir / "texcache").mkdir()
    env = worker_env(
        "engine",
        tmpdir=str(workdir),
        extra={
            "LUAINPUTS": f"{_tex_path(TEMPLATES)}{os.pathsep}",
            "TEXMFCACHE": "texcache" if has_document_files else os.environ.get("TEXMFCACHE", "texcache"),
        },
    )
    process = subprocess.run(
        [str(engine) if engine is not None else "lualatex", "--no-shell-escape", "--halt-on-error", "--interaction=batchmode",
         _tex_path(TEMPLATES / "book.tex")],
        cwd=workdir, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if process.returncode:
        diagnostic = workdir / "layout-error.txt"
        reason = diagnostic.read_bytes() if diagnostic.is_file() else b""
        parts = reason.split(b":")
        if (len(parts) == 3 and parts[0] == b"running" and parts[1] in (b"header", b"footer")
                and parts[2].isdigit() and len(parts[2]) <= 9):
            role = parts[1].decode("ascii")
            name = "머리글" if role == "header" else "바닥글"
            raise InputError({role: f"{int(parts[2])}쪽 {name}의 칸이 겹치거나 여백을 벗어납니다."})
        if len(parts) == 2 and parts[0] == b"line_numbers" and parts[1].isdigit() and len(parts[1]) <= 9:
            raise InputError({"line_numbers": f"{int(parts[1])}쪽에 줄 번호를 놓을 여백이 부족합니다."})
        if reason.startswith(b"list_width:"):
            level = reason.removeprefix(b"list_width:")
            if level.isdigit() and len(level) <= 6:
                raise InputError({"list_levels": f"{int(level)}단계 목록 본문을 놓을 폭이 부족합니다."})
        if reason == b"table_width":
            raise InputError({"style": "표의 열을 놓을 폭이 부족합니다. 열 수를 줄이거나 판형을 넓혀 주세요."})
        if reason == b"table_height":
            raise InputError({"style": "표의 제목과 본문을 놓을 높이가 부족합니다. 제목을 줄이거나 판형을 키워 주세요."})
        if reason == b"frame_height":
            raise InputError({"text_styles.heading1.frame": "제목 틀이 한 쪽의 본문 높이를 넘습니다. 제목 글자 크기를 줄이거나 판형을 키워 주세요."})
        if reason == b"gloss_size":
            raise InputError({"text_styles.gloss.font_pt": "풀이를 작게 표시할 수 있도록 대상 글자의 크기를 키워 주세요."})
        if reason == b"line_width":
            raise InputError({"style": "한 글자 또는 함께 놓을 묶음이 줄 폭을 넘습니다. 들여쓰기나 여백을 줄이거나 판형을 넓혀 주세요."})
        process.check_returncode()
    pdf = workdir / "book.pdf"
    if max_pdf_bytes is not None and pdf.stat().st_size > max_pdf_bytes:
        raise ValueError("PDF exceeds size limit")
    return pdf.read_bytes()
