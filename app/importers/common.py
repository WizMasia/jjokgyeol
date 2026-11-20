"""Shared Markdown output and per-document resource limits."""
from dataclasses import dataclass
from itertools import groupby

from app.configuration import CONFIG_DIR, read_config


LIMITS = read_config(CONFIG_DIR / "imports.json")


@dataclass
class ImportResult:
    markdown: str
    warnings: list[str]


def styled(runs):
    """Join split Office runs before emitting emphasis delimiters."""
    pieces = []
    for (bold, italic), group in groupby(runs, key=lambda r: r[1:]):
        text = "".join(run[0] for run in group)
        marker = "*" * (2 * bold + italic)
        if marker and text.strip():
            start = len(text) - len(text.lstrip())
            end = len(text.rstrip())
            text = text[:start] + marker + text[start:end] + marker + text[end:]
        pieces.append(text)
    return "".join(pieces)


class Manuscript:
    def __init__(self):
        self.parts = []
        self.warnings = {}
        self.size = 0
        self.list_path = []  # Source depths and Markdown marker widths of actual ancestors.
        self.records = 0

    def count(self):
        self.records += 1
        if self.records > LIMITS["max_records"]:
            raise ValueError("원고의 구조 요소 수가 한도를 넘었습니다.")

    def warn(self, message):
        self.warnings[message] = None

    def block(self, text, kind="body", depth=1):
        self.count()
        text = text.strip()
        if not text:
            return
        sep = "\n\n" if self.parts else ""
        if kind in ("ordered", "bullet"):
            if self.list_path:
                sep = "\n"
            while self.list_path and self.list_path[-1][0] >= depth:
                self.list_path.pop()
            actual = len(self.list_path) + 1
            if actual > 9:
                raise ValueError("편집기에서 열 수 있는 목록 중첩 한도를 넘었습니다.")
            if actual != depth:
                self.warn(f"목록의 {depth}단계를 기존 항목 아래 {actual}단계로 옮겼습니다.")
            marker = "1. " if kind == "ordered" else "- "
            text = " " * sum(width for _, width in self.list_path) + marker + text
            self.list_path.append((depth, len(marker)))
        else:
            self.list_path.clear()
            if kind == "heading":
                if depth > 6:
                    self.warn(f"제목의 {depth}단계를 마크다운 6단계로 옮겼습니다.")
                text = "#" * min(depth, 6) + " " + text
        self.size += len(text.encode()) + len(sep)
        if self.size > LIMITS["max_output_bytes"]:
            raise ValueError("변환 원고 크기가 한도를 넘었습니다.")
        self.parts.extend((sep, text))

    def finish(self):
        if not self.parts:
            raise ValueError("가져올 본문이 없습니다.")
        return ImportResult("".join(self.parts), list(self.warnings))
