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
    for section, part in enumerate(parts, 1):
        root = _xml(z, part)
        if root.tag != "sec":
            raise ValueError("HWPX 구역의 형식이 올바르지 않습니다.")
        for index, p in enumerate(_hwp_paragraphs(root), 1):
            location = f"HWPX 구역 {section} 문단 {index}"
            style = styles.get(p.get("styleIDRef", "0"))
            defaults = {} if style is None else style.attrib
            props = paras.get(p.get("paraPrIDRef", defaults.get("paraPrIDRef")))
            heading = _attrs(props, "heading")
            kind = {"BULLET": "bullet", "NUMBER": "ordered", "OUTLINE": "heading"}.get(heading.get("type"), "body")
            runs = []
            for run_index, run in enumerate(p.findall("run"), 1):
                char = chars.get(run.get("charPrIDRef", defaults.get("charPrIDRef")))
                bold = char is not None and char.find("bold") is not None
                italic = char is not None and char.find("italic") is not None
                for item, node in enumerate(run, 1):
                    position = f"{location} 구간 {run_index} 요소 {item}"
                    if node.tag == "t":
                        pieces = [node.text or ""]
                        for child in node:
                            if child.tag in ("tab", "lineBreak", "fwSpace", "nbSpace"):
                                pieces.append(" ")
                            pieces.append(child.tail or "")
                        runs.append(("".join(pieces), bold, italic))
                    elif node.tag == "pic":
                        out.warn(f"{position}의 그림을 생략했습니다.")
                    elif node.tag == "tbl":
                        out.warn(f"{position}의 표 셀을 문단으로 옮겼습니다.")
                    elif node.tag == "ctrl":
                        for field in node:
                            if field.tag in ("footNote", "endNote"):
                                out.warn(f"{position}의 각주·미주를 생략했습니다.")
                            elif field.tag == "autoNum" and field.get("numType") not in ("PAGE", "TOTAL_PAGE"):
                                fmt = _attrs(field, "autoNumFormat")
                                number = field.attrib["num"]
                                if fmt.get("type", "DIGIT") != "DIGIT":
                                    out.warn(f"{position}의 자동 번호를 숫자로 옮겼습니다.")
                                text = fmt.get("prefixChar", "") + number + fmt.get("suffixChar", "")
                                runs.append((text, bold, italic))
            text = styled(runs, lambda msg: out.warn(f"{location} · {msg}"))
            out.block(text, kind, int(heading.get("level", 0)) + 1, location=location)
