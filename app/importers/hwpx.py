"""Read Hancom XML sections in spine order."""
from app.reference_office import _attrs, _hwp_paragraphs, _hwp_parts, _xml
from .common import styled


def read(z, out):
    head, parts = _hwp_parts(z)
    if head.tag != "head" or not parts:
        raise ValueError("HWPX 구역 또는 속성 정의를 찾지 못했습니다.")

    def indexed(path):
        return {n.get("id"): n for n in head.findall("refList/" + path)}

    styles = indexed("styles/style")
    chars = indexed("charProperties/charPr")
    paras = indexed("paraProperties/paraPr")
    for part in parts:
        root = _xml(z, part)
        if root.tag != "sec":
            raise ValueError("HWPX 구역의 형식이 올바르지 않습니다.")
        for p in _hwp_paragraphs(root):
            style = styles.get(p.get("styleIDRef", "0"))
            defaults = {} if style is None else style.attrib
            props = paras.get(p.get("paraPrIDRef", defaults.get("paraPrIDRef")))
            heading = _attrs(props, "heading")
            kind = {"BULLET": "bullet", "NUMBER": "ordered", "OUTLINE": "heading"}.get(heading.get("type"), "body")
            runs = []
            for run in p.findall("run"):
                char = chars.get(run.get("charPrIDRef", defaults.get("charPrIDRef")))
                bold = char is not None and char.find("bold") is not None
                italic = char is not None and char.find("italic") is not None
                for node in run:
                    if node.tag == "t":
                        pieces = [node.text or ""]
                        for child in node:
                            if child.tag in ("tab", "lineBreak", "fwSpace", "nbSpace"):
                                pieces.append(" ")
                            pieces.append(child.tail or "")
                        runs.append(("".join(pieces), bold, italic))
                    elif node.tag == "pic":
                        out.warn("HWPX 본문의 그림을 생략했습니다.")
                    elif node.tag == "tbl":
                        out.warn("HWPX 표의 셀을 문단으로 옮겼습니다.")
                    elif node.tag == "ctrl" and any(c.tag in ("footNote", "endNote") for c in node):
                        out.warn("HWPX 각주·미주를 생략했습니다.")
            out.block(styled(runs), kind, int(heading.get("level", 0)) + 1)
