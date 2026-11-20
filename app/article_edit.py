"""Toggle a heading's pin in its original Markdown source."""

import json
import re
import sys

from app.articles import match_article
from app.document import InputError, heading_text, heading_tokens


def _unpin(inline, marked_length):
    """Cut the visible suffix, retaining closers for emphasis opened in the title."""
    cut = len(inline.content)
    closers = []
    for token in reversed(inline.children):
        if marked_length == 0:
            break
        if token.nesting:
            cut -= len(token.markup)
            if token.nesting < 0:
                closers.append(token.markup)
            else:
                closers.pop()
        else:
            # Only whitespace and the literal pin can occur in the matched suffix.
            size = min(marked_length, len(token.content))
            marked_length -= size
            cut -= size
    return cut, "".join(reversed(closers))


def _edit_heading(source, heading, inline, text, pin):
    # Split only Markdown line endings. Keep every delimiter for exact reassembly.
    parts = re.split(r"(\r\n|\r|\n)", source)
    end_row = inline.map[1] - 1
    while not parts[2 * end_row].strip():
        end_row -= 1
    content = inline.content
    last_line = content.rsplit("\n", 1)[-1].lstrip(" \t")
    line = parts[2 * end_row]
    # The parser already removed ATX closing hashes, indentation and trailing spaces.
    end = line.index(last_line) + len(last_line)
    if pin is None:
        parts[2 * end_row] = line[:end] + " {번호고정}" + line[end:]
    else:
        cut, closers = _unpin(inline, len(text) - pin)
        removed = content[cut:]
        start_row = end_row - removed.count("\n")
        if start_row == end_row:
            title = line[:end - len(removed)] + closers
            # A literal final hash needs its own ATX closer after removing the pin.
            if heading.markup.startswith("#") and not line[end:].strip() and re.search(r"[ \t]#+$", title):
                title += " #"
            parts[2 * end_row] = title + line[end:]
        else:
            # A Setext pin on its own line must take its line with it, up to the underline.
            previous = parts[2 * start_row]
            column = len(previous) - len(removed.partition("\n")[0])
            parts[2 * start_row] = previous[:column] + closers + previous[len(previous.rstrip()):]
            del parts[2 * start_row + 2:2 * end_row + 2]
    return "".join(parts)


def toggle_pin(source: str, first_line: int, last_line: int) -> str:
    invalid = "원고에서 번호를 고정하거나 해제할 조 제목을 선택하세요."
    if type(first_line) is not int or type(last_line) is not int or not 0 <= first_line <= last_line:
        raise InputError({"source": invalid})
    if not isinstance(source, str):
        raise InputError({"source": "마크다운 원고는 문자열이어야 합니다."})
    for heading, inline in heading_tokens(source.removeprefix("\ufeff")):
        start, end = heading.map
        if start <= first_line <= last_line < end:
            text = heading_text(inline)
            match = match_article(text)
            if match is not None:
                return _edit_heading(source, heading, inline, text, match[1])
            break
    raise InputError({"source": invalid})


if __name__ == "__main__":
    from app.jobs import INVALID_INPUT

    try:
        values = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        if not isinstance(values, dict) or values.keys() != {"source", "first_line", "last_line"}:
            raise ValueError
        result = toggle_pin(**values)
    except InputError as error:
        sys.stdout.buffer.write(error.message.encode())
        sys.exit(INVALID_INPUT)
    except (ValueError, RecursionError):
        sys.stdout.buffer.write("원고와 선택한 줄을 확인하세요.".encode())
        sys.exit(INVALID_INPUT)
    sys.stdout.buffer.write(json.dumps({"source": result}, ensure_ascii=False).encode())
