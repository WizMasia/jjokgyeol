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
from app.list_marks import format_mark
from app.publication import FONT_DIR, engine_policy
from app.reference_rules import block_values, running_contexts
from app.running_heads import RUNNING_ROLES, SLOTS, parse_running_text
from app.style import RUNNING_DEFAULTS, TEXT_DEFAULTS

if TYPE_CHECKING:
    from app.document import Document
    from app.style import Style

TEMPLATES = Path(__file__).resolve().with_name("tex")


def typeset(doc: Document, style: Style, workdir: Path, *, collect_candidates: bool = False,
            paragraph_choices: dict[int, str] | None = None) -> bytes:
    """Optionally collect paragraph-candidates.jsonl or replay its per-block IDs."""
    return _render(doc, style, workdir, None, collect_candidates, paragraph_choices)


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


def _render(doc, style, workdir, candidate, collect_candidates=False, paragraph_choices=None):
    if any(workdir.iterdir()):
        raise ValueError("조판에는 빈 작업 폴더가 필요합니다.")
    levels = style.resolved_list_levels()
    overrides = block_values(doc.blocks, style, fenced_blocks=doc.fenced_blocks)
    role_styles = _role_styles(style)
    for index, block in enumerate(doc.blocks):
        role = f"heading{block.level}" if block.kind == "heading" else "body"
        overrides[index] = role_styles[role] | overrides[index]
    settings, ids = _running_sections(doc, style)
    doc, overrides = doc.render_layout(overrides, number_articles=style.article_expr is not None, running_ids=ids)
    blocks = [asdict(block) for block in doc.blocks]
    number_articles(blocks, style.article_expr, doc.fenced_blocks)
    box_ids = {}
    for block, values in zip(blocks, overrides, strict=True):
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
        if block["first"]:
            try:
                mark = format_mark(values.get(prefix + "expr", row.expr), block["ordinal"])
            except ValueError as error:
                raise InputError({f"list_levels[{index}].expr": f"{index + 1}단계 표지를 만들 수 없습니다."}) from error
            key = (mark, values.get("font", style.body_font), values.get("font_sp", style.body_sp),
                   values.get("color", TEXT_DEFAULTS["color"]), values.get("weight"), values.get("slant"))
            block["mark"] = mark
            block["mark_id"] = box_ids.setdefault(key, len(box_ids) + 1)
    if doc.memos:
        printable = False
        for block in blocks:
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
    data["text_defaults"] = TEXT_DEFAULTS
    if doc.endnotes:
        data["endnotes"] = doc.endnotes
    if settings:
        data["running_sections"] = settings
    if collect_candidates or paragraph_choices is not None:
        snapshot = json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        data["paragraphs"] = {"snapshot": hashlib.sha256(snapshot).hexdigest(),
                              "collect": collect_candidates, "choices": paragraph_choices or {}}
    (workdir / "input.json").write_text(
        json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    font_dir = Path(os.environ.get("JJOKGYEOL_FONT_DIR", FONT_DIR))
    (workdir / "fonts").symlink_to(font_dir.resolve(strict=True), target_is_directory=True)
    (workdir / "texcache").mkdir()
    env = dict(os.environ, LUAINPUTS=f"{TEMPLATES}{os.pathsep}",
               TEXMFCACHE=os.environ.get("TEXMFCACHE", "texcache"))
    process = subprocess.run(
        ["lualatex", "--no-shell-escape", "--halt-on-error", "--interaction=batchmode",
         str(TEMPLATES / "book.tex")],
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
        if reason.startswith(b"list_width:"):
            level = reason.removeprefix(b"list_width:")
            if level.isdigit() and len(level) <= 6:
                raise InputError({"list_levels": f"{int(level)}단계 목록 본문을 놓을 폭이 부족합니다."})
        if reason == b"frame_height":
            raise InputError({"text_styles.heading1.frame": "제목 틀이 한 쪽의 본문 높이를 넘습니다. 제목 글자 크기를 줄이거나 판형을 키워 주세요."})
        if reason == b"gloss_size":
            raise InputError({"text_styles.gloss.font_pt": "풀이를 작게 표시할 수 있도록 대상 글자의 크기를 키워 주세요."})
        if reason == b"line_width":
            raise InputError({"style": "한 글자 또는 함께 놓을 묶음이 줄 폭을 넘습니다. 들여쓰기나 여백을 줄이거나 판형을 넓혀 주세요."})
        process.check_returncode()
    return (workdir / "book.pdf").read_bytes()
