"""Read the Word main story, resolving paragraph styles and run emphasis."""
import posixpath
import re
from xml.etree import ElementTree as ET

from app.reference_office import (_attrs, _events, _numpr, _rels, _val, _WordStyles,
                                 _WordNumbering, _wprops, _xml)
from .common import LIMITS, styled


def _switch(element, name, default=False):
    node = element.find(name) if element is not None else None
    return default if node is None else node.get("val", "true").lower() not in ("false", "0", "off")


class _Emphasis:
    def __init__(self, styles):
        self.styles = styles
        self.cache = {}

    def toggles(self, key):
        chain = []
        while key in self.styles and key not in self.cache:
            if key in chain or len(chain) >= LIMITS["max_nesting"]:
                raise ValueError("DOCX 스타일 상속이 순환하거나 너무 깊습니다.")
            chain.append(key)
            key = _attrs(self.styles[key], "basedOn").get("val")
        flags = self.cache.get(key, (False, False))
        for key in reversed(chain):
            rpr = self.styles[key].find("rPr")
            flags = (flags[0] ^ _switch(rpr, "b"), flags[1] ^ _switch(rpr, "i"))
            self.cache[key] = flags
        return flags


_PAGE = re.compile(r"\s*(PAGE|NUMPAGES|SECTIONPAGES)\b", re.I)
_SKIP = {"drawing", "pict", "object", "txbxContent", "del", "moveFrom"}


def _runs(p, base, emphasis, fields, out, location):
    bold, italic = base
    simple = []
    objects = 0
    for node, opening in _events(p, _SKIP):
        tag = node.tag
        if not opening:
            if tag == "fldSimple":
                simple.pop()
            continue
        out.count()
        if tag == "fldSimple":
            simple.append(bool(_PAGE.match(node.get("instr", ""))))
        elif tag == "fldChar":
            kind = node.get("fldCharType")
            if kind == "begin":
                fields.append(["", False, False])  # instruction, showing result, page field
            elif fields and kind == "separate":
                fields[-1][1:] = [True, bool(_PAGE.match(fields[-1][0]))]
            elif fields and kind == "end":
                fields.pop()
        elif tag == "instrText" and fields and not fields[-1][1]:
            fields[-1][0] = (fields[-1][0] + (node.text or ""))[:128]
        elif tag in ("drawing", "pict", "object"):
            objects += 1
            out.warn(f"{location}의 그림·개체 {objects} 생략")
        elif tag == "r":
            rpr = node.find("rPr")
            flags = emphasis.toggles(_attrs(rpr, "rStyle").get("val"))
            bold = _switch(rpr, "b", base[0] ^ flags[0])
            italic = _switch(rpr, "i", base[1] ^ flags[1])
        elif tag in ("t", "br", "cr", "tab") and not any(simple) and not any(
                not result or page for _, result, page in fields):
            yield ((node.text or "") if tag == "t" else " ", bold, italic)
        if len(simple) + len(fields) > LIMITS["max_nesting"]:
            raise ValueError("DOCX 필드 중첩이 너무 깊습니다.")


def read(z, out):
    main = _rels(z, "").get("officeDocument", "word/document.xml")
    root = _xml(z, main)
    body = root.find("body")
    if root.tag != "document" or body is None:
        raise ValueError("DOCX 본문을 찾을 수 없습니다.")
    refs = _rels(z, main)

    def part(kind):
        path = refs.get(kind, posixpath.join(posixpath.dirname(main), kind + ".xml"))
        return _xml(z, path) if path in z.NameToInfo else ET.Element(kind)

    style_root = part("styles")
    styles = _WordStyles(style_root)
    emphasis = _Emphasis(styles.styles)
    defaults = style_root.find("docDefaults/rPrDefault/rPr")
    numbering = _WordNumbering(part("numbering"))
    fields = []
    paragraph = 0
    table = 0
    for p, opening in _events(body, _SKIP | {"p"}):
        if not opening:
            continue
        if p.tag == "tbl":
            table += 1
            out.warn(f"DOCX 본문 표 {table}의 셀을 문단으로 옮겼습니다.")
        if p.tag != "p":
            continue
        paragraph += 1
        location = f"DOCX 본문 문단 {paragraph}"
        ppr = p.find("pPr")
        key = _attrs(ppr, "pStyle").get("val", styles.defaults.get("paragraph"))
        flags = emphasis.toggles(key)
        base = (_switch(defaults, "b") ^ flags[0], _switch(defaults, "i") ^ flags[1])
        runs = _runs(p, base, emphasis, fields, out, location)
        text = styled(runs, lambda msg: out.warn(f"{location} · {msg}"))
        props = styles.base_p | styles.resolve(key)[0] | _wprops(ppr, "p") | _numpr(ppr)
        outline = _val(props, "outlineLvl")
        style_name = _attrs(styles.styles.get(key), "name").get("val", key or "")
        heading = re.fullmatch(r"(?:heading|제목)\s*([1-9])", style_name, re.I)
        num = _val(props, "numId")
        if outline is not None and 0 <= int(outline) < 9:
            out.block(text, "heading", int(outline) + 1, location=location)
        elif outline is None and heading:
            out.block(text, "heading", int(heading[1]), location=location)
        elif num and num != "0":
            level = _val(props, "ilvl", default="0")
            fmt = _attrs(numbering.level(num, level), "numFmt").get("val")
            out.block(text, "bullet" if fmt == "bullet" else "ordered", int(level) + 1, location=location)
        else:
            out.block(text)
