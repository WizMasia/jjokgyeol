"""Toggle a heading's pin in its original Markdown source."""

import json
from pathlib import Path
import re
import sys

if __name__ == "__main__":
    # -I는 현재 작업 디렉터리를 제외하므로 설치 경로만 추가합니다. / -I excludes cwd; import only from the installed app.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.document import InputError, heading_title, heading_tokens
from app.style import ALIGNMENT_ROLES


def _unpin(inline, text, pin):
    """Find cuts backwards from the raw end; text before the pin may contain escapes."""
    content = inline.content
    raw = len(content)
    visible = len(text)
    marker = text.index("{", pin)
    end = len(text.rstrip())
    cuts = []
    closes = []
    kept = 0
    for token in reversed(inline.children):
        if token.type == "memo_ref":
            if visible <= marker:
                break  # Keep whitespace on the title side of a memo.
            raw -= len(token.markup)
            kept += 1
        elif token.nesting:
            start = raw - len(token.markup)
            if token.nesting < 0:
                if pin < visible <= marker:
                    pin = visible  # Keep spaces inside a label that closes before the pin.
                closes.append((start, raw, kept))
            else:
                left, right, before = closes.pop()
                if before == kept:
                    cuts.extend([(start, raw), (left, right)])
                elif marker <= visible <= end:
                    # A surviving emphasis opener around a memo still needs its separator.
                    pin = marker
            raw = start
        elif token.type == "text":
            start = visible - len(token.content)
            left, right = max(pin, start), min(end, visible)
            if left < right:
                newline = token.content.rfind("\n", left - start, right - start)
                if newline >= 0:
                    pin = left = start + newline + 1
                if left < right:
                    cuts.append((raw - (visible - left), raw - (visible - right)))
            kept += len(token.content) - max(0, right - left)
            raw -= len(token.markup or token.content)
            visible = start
        else:
            break  # A gloss or printed note cannot occur within the marker.
        if visible < pin:
            break

    # Cuts are confined to the marker line. Copy every other character from the
    # manuscript, including memo spellings and punctuation consumed by read_escape.
    cuts.sort()
    line_start = content.rfind("\n", 0, cuts[0][0]) + 1
    old = content[line_start:].partition("\n")[0]
    pieces = []
    pos = line_start
    for left, right in cuts:
        pieces.append(content[pos:left])
        pos = right
    pieces.append(content[pos:line_start + len(old)])
    new = "".join(pieces)
    closers = "".join(content[a:b] for a, b, _ in reversed(closes)
                      if line_start <= a < line_start + len(old))
    return content.count("\n", 0, line_start), old, new, closers


def _edit_heading(source, heading, inline, text, pin):
    # Edit each affected physical line in place, preserving delimiters and ATX syntax.
    parts = re.split(r"(\r\n|\r|\n)", source)
    if pin is None:
        if inline.children[-1].meta.get("unfinished_gloss"):
            raise InputError({"source": "제목 끝의 풀이를 닫은 뒤 번호를 고정하세요."})
        index = inline.content.count("\n")
        old = inline.content.rsplit("\n", 1)[-1]
        new = old + " {번호고정}"
        closers = ""
    else:
        index, old, new, closers = _unpin(inline, text, pin)

    row = 2 * (inline.map[0] + index)
    line = parts[row]
    column = line.index(old)
    title = line[:column] + new
    tail = line[column + len(old):]
    if pin is not None and index and new == closers:
        # A Setext pin on its own line can also close emphasis in the title.
        previous = parts[row - 2]
        body = previous.rstrip(" \t")
        parts[row - 2] = body + closers + previous[len(body):]
        del parts[row:row + 2]
    else:
        if pin is not None and heading.markup.startswith("#") and not tail.strip() and re.search(r"[ \t]#+$", title):
            title += " #"
        parts[row] = title + tail
    return "".join(parts)


def toggle_pin(source: str, first_line: int, last_line: int, aligned_roles=()) -> str:
    invalid = "원고에서 번호를 고정하거나 해제할 조 제목을 선택하세요."
    if type(first_line) is not int or type(last_line) is not int or not 0 <= first_line <= last_line:
        raise InputError({"source": invalid})
    if not isinstance(source, str):
        raise InputError({"source": "마크다운 원고는 문자열이어야 합니다."})
    if (not isinstance(aligned_roles, (tuple, list)) or len(aligned_roles) > len(ALIGNMENT_ROLES)
            or any(role not in ALIGNMENT_ROLES for role in aligned_roles)):
        raise InputError({"source": "정렬을 지정한 본문·제목 단계를 확인하세요."})
    for heading, inline in heading_tokens(source.removeprefix("\ufeff"), aligned_roles):
        start, end = heading.map
        if start <= first_line <= last_line < end:
            title = heading_title(inline)
            match = title.article()
            if match is not None:
                return _edit_heading(source, heading, inline, title.text, match[1])
            break
    raise InputError({"source": invalid})


if __name__ == "__main__":
    from app.configuration import unique_fields
    from app.jobs import INVALID_INPUT
    from app.operations import LIMITS
    from app.security import harden_worker, reject_json_constant

    harden_worker(max_file_bytes=LIMITS.worker_file_bytes)
    try:
        raw = sys.stdin.buffer.read(LIMITS.request_body_bytes + 1)
        if len(raw) > LIMITS.request_body_bytes:
            raise ValueError
        values = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=unique_fields,
            parse_constant=reject_json_constant,
        )
        required = {"source", "first_line", "last_line"}
        if not isinstance(values, dict) or not required <= values.keys() <= required | {"aligned_roles"}:
            raise ValueError
        result = toggle_pin(**values)
    except InputError as error:
        sys.stdout.buffer.write(error.message.encode())
        sys.exit(INVALID_INPUT)
    except (ValueError, RecursionError):
        sys.stdout.buffer.write("원고와 선택한 줄을 확인하세요.".encode())
        sys.exit(INVALID_INPUT)
    encoded = json.dumps({"source": result}, ensure_ascii=False, allow_nan=False).encode()
    if len(encoded) > LIMITS.worker_file_bytes:
        sys.exit(1)
    sys.stdout.buffer.write(encoded)
