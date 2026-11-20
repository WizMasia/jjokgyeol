"""Expand list labels as literal text for the fixed Nanum publication fonts."""
import re


_HANGUL = "가나다라마바사아자차카타파하"
# Both locked Nanum families have these twenty circled digits. Use readable
# decimal parentheses beyond them so changing the body font cannot lose a mark.
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
_TOKENS = re.compile(r"{{|}}|\{숫자\}|\{한글\}|\{원숫자\}|\{계층번호\}|[{}]")


def uses_hierarchy(expr: str) -> bool:
    return any(match[0] == "{계층번호}" for match in _TOKENS.finditer(expr))


def _hangul(ordinal: int) -> str:
    # Bijective base 14: 가 … 하, 가가, 가나 … 하하, 가가가.
    letters = []
    while ordinal:
        ordinal, index = divmod(ordinal - 1, len(_HANGUL))
        letters.append(_HANGUL[index])
    return "".join(reversed(letters))


def format_mark(expr: str, ordinal: int, *, parents: tuple[int, ...] | None = None) -> str:
    """Return one label; the style validator associates errors with its level."""
    if not isinstance(expr, str) or re.search(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff]", expr):
        raise ValueError("표지 표현식에 조판할 수 없는 문자가 있습니다.")
    if type(ordinal) is not int or ordinal < 1:
        raise ValueError("표지 순번은 1 이상의 정수여야 합니다.")

    def expand(match):
        token = match[0]
        if token in ("{{", "}}"):
            return token[0]
        if token == "{계층번호}" and parents is not None:
            return ".".join(map(str, (*parents, ordinal)))
        if token == "{숫자}":
            return str(ordinal)
        if token == "{한글}":
            return _hangul(ordinal)
        if token == "{원숫자}":
            return _CIRCLED[ordinal - 1] if ordinal <= len(_CIRCLED) else f"({ordinal})"
        raise ValueError("표지의 자리표시자 이름이나 중괄호 짝을 확인하세요.")

    return _TOKENS.sub(expand, expr)
