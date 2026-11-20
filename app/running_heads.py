"""One-line header/footer expressions, kept separate from manuscript text."""

import re


RUNNING_ROLES = ("header", "footer")
RUNNING_FACES = ("default", "first", "odd", "even")
SLOTS = ("left", "center", "right")
_TOKENS = re.compile(r"{{|}}|\{현재쪽\}|[{}\x00-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff]")


def escape_running_literal(text: str) -> str:
    return text.replace("{", "{{").replace("}", "}}")


def parse_running_text(text: str) -> list[tuple[str, str]]:
    if not isinstance(text, str):
        raise ValueError("한 줄 문구를 문자열로 입력하세요.")
    tokens = []
    literal = []
    end = 0
    for match in _TOKENS.finditer(text):
        if match.start() > end:
            literal.append(text[end:match.start()])
        token = match[0]
        if token == "{현재쪽}":
            if literal:
                tokens.append(("text", "".join(literal)))
                literal.clear()
            tokens.append(("page", ""))
        elif token in ("{{", "}}"):
            literal.append(token[0])
        else:
            reason = ("{현재쪽} 필드나 중괄호 짝을 확인하세요." if token in "{}" else
                      "줄바꿈이나 제어 문자는 사용할 수 없습니다.")
            raise ValueError(f"{match.start() + 1}번째 글자: {reason}")
        end = match.end()
    if end < len(text):
        literal.append(text[end:])
    if literal:
        tokens.append(("text", "".join(literal)))
    return tokens
