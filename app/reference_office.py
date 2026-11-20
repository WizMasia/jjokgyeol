"""Read Office formatting where it occurs, with evidence tied to source paragraphs."""
from dataclasses import dataclass
from io import BytesIO
from itertools import islice
import math
import posixpath
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile
from zlib import error as CompressionError

from app.publication import FONT_LABELS
from app.configuration import CONFIG_DIR, read_config
from app.running_heads import SLOTS, escape_running_literal


TWIP_MM = 25.4 / 1440
HWP_MM = 25.4 / 7200
WORD = {"http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "http://purl.oclc.org/ooxml/wordprocessingml/main"}
FONT_IDS = {name.lower().replace("-", "").replace(" ", ""): key
            for key, label in FONT_LABELS.items() for name in (label, key)}


class _NoDoctype(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise ValueError("참고 문서 XML에서 DTD를 사용할 수 없습니다.")


def _xml(z, path):
    if path not in z.NameToInfo:
        raise ValueError("참고 문서에 필요한 XML 파일이 없습니다.")
    root = ET.fromstring(z.read(path), parser=ET.XMLParser(target=_NoDoctype()))
    # Keep drawing/foreign elements distinct from Word paragraphs and runs.
    for node in root.iter():
        ns, _, tag = node.tag.rpartition("}")
        if ns.lstrip("{") in WORD or not ns or any(s in ns for s in (
            "hancom.co.kr/hwpml/", "owpml.org/owpml/", "/relationships", "/opf", "/container",
            "/drawingml/2006/main", "/drawingml/main",
        )):
            node.tag = tag
        node.attrib = {key.rsplit("}", 1)[-1]: val for key, val in node.attrib.items()}
    return root


def _number(value):
    x = float(value)
    if not math.isfinite(x):
        raise ValueError("서식 수치가 올바르지 않습니다.")
    return x


def _dimensions(node, keys, scale):
    if node is not None and all(node.get(k) is not None for k in keys):
        return [round(_number(node.get(k)) * scale, 6) for k in keys]


def _attrs(node, path):
    el = node.find(path) if node is not None else None
    return el.attrib if el is not None else {}


def _scripts(text):
    result = []
    for script, pattern in (("hangul", r"[가-힣㄰-㆏ᄀ-ᇿ]"), ("hanja", r"[㐀-鿿]"),
                            ("japanese", r"[ぁ-ヿ]"), ("complex", r"[\u0590-\u08ff\u0900-\u0dff]"),
                            ("latin", r"[A-Za-zÀ-ʯ]")):
        if re.search(pattern, text):
            result.append(script)
    return result or ["latin"]


def _expr(pattern, token, marker):
    # A parent counter cannot be represented by the current-level expression grammar.
    if marker.startswith("^") and re.search(r"\^[nN]", pattern):
        return None
    placeholders = re.findall(re.escape(marker[0]) + r"[1-9]", pattern)
    if any(p != marker for p in placeholders) or (placeholders and token is None):
        return None
    return pattern.replace("{", "{{").replace("}", "}}").replace(marker, token or "")


class _Unit:
    def __init__(self, location, section, excerpt, source, settings, warnings):
        self.limit = settings["max_candidates_per_unit"]
        self.evidence_chars = settings["max_evidence_chars"]
        self.warnings = warnings
        self.source = f"{location} · {source[:self.evidence_chars]}"
        self.data = dict(location=location, section=section, excerpt=excerpt, candidates=[])
        self.seen = set()

    def full(self):
        if len(self.seen) < self.limit:
            return False
        self.warnings.add("문단별 서식 후보 한도에 도달해 이후 후보 판독을 생략했습니다.")
        return True

    def add(self, role, key, value, evidence, basis="read", **metadata):
        if value is None or self.full():
            return
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("서식 수치가 너무 큽니다.")
            value = round(value, 6)
        identity = (role, key, tuple(value) if isinstance(value, list) else value, basis, metadata.get("source_group"))
        if identity not in self.seen:
            self.seen.add(identity)
            proof = f"{self.source} · {evidence[:self.evidence_chars]}"
            if len(proof) > self.evidence_chars:
                self.warnings.add("서식 근거 설명을 표시 길이 한도에서 줄였습니다.")
            self.data["candidates"].append(dict(role=role, key=key, value=value, basis=basis,
                                                evidence=proof[:self.evidence_chars], **metadata))

    def font(self, role, name, evidence):
        if name:
            key = FONT_IDS.get(name.lower().replace("-", "").replace(" ", ""))
            self.add(role, "font", key or name, evidence, "read" if key else "confirm")


def _excerpt(runs, limit, warnings):
    pieces = []
    available = limit
    for run in runs:
        for t in run.iterfind("t"):
            for chunk in t.itertext():
                if not pieces:
                    chunk = chunk.lstrip()
                if chunk:
                    pieces.append(chunk[:available])
                    if len(chunk) > available:
                        warnings.add("원문 발췌를 표시 길이 한도에서 줄였습니다.")
                        return "".join(pieces)
                    available -= len(chunk)
    return "".join(pieces)


def _section(name, excerpt, role, first, previous):
    name = name.lower()
    if name.startswith(("toc", "목차", "차례", "contents")) or excerpt.strip() in ("목차", "차례"):
        return "목차"
    if name in ("title", "표제", "표지", "제목") or (first and role == "heading"):
        return "표지"
    if previous == "목차" and role != "heading":
        return "목차"
    return "본문"


def _relationships(z, part):
    source = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
    if source not in z.NameToInfo:
        return {}
    result = {}
    for rel in _xml(z, source).findall("Relationship"):
        if rel.get("TargetMode") == "External":
            continue
        target = rel.get("Target", "")
        path = posixpath.normpath(posixpath.join(posixpath.dirname(part), target)).lstrip("/")
        result[rel.get("Id")] = (rel.get("Type", "").rsplit("/", 1)[-1], path)
    return result


def _rels(z, part):
    return dict(_relationships(z, part).values())


def _wprops(element, label):
    if element is None:
        return {}
    props = {(child.tag, attr): (val, label) for child in element if child.tag in
             ("sz", "szCs", "rFonts", "color", "ind", "spacing", "outlineLvl", "ilvl", "numId", "jc")
             for attr, val in child.attrib.items()}
    if element.tag == "pPr":
        for edge in element.iterfind("pBdr/*"):
            props["pBdr", edge.tag] = (edge.get("val") not in ("nil", "none"), label)
        shading = element.find("shd")
        if shading is not None:
            pattern = shading.get("val", "clear")
            paint = pattern != "clear" or shading.get("fill", "auto") != "auto" or "themeFill" in shading.attrib
            props["shd", "fill"] = (pattern != "nil" and paint, label)
    return props


def _props_merge(base, extra):
    props = base.copy()
    for tag, attr in extra:
        # These attributes describe alternative representations of the same property.
        for group in (("firstLine", "hanging", "firstLineChars", "hangingChars"),
                      ("left", "start", "leftChars", "startChars"),
                      ("eastAsia", "eastAsiaTheme"), ("ascii", "asciiTheme"), ("hAnsi", "hAnsiTheme"),
                      ("cs", "cstheme", "csTheme")):
            if attr in group:
                for other in group:
                    props.pop((tag, other), None)
        if tag == "color":
            for old in ("val", "themeColor", "themeTint", "themeShade"):
                props.pop((tag, old), None)
    props.update(extra)
    return props


def _numpr(ppr):
    return _wprops(ppr.find("numPr"), "numPr") if ppr is not None else {}


def _val(props, tag, attr="val", default=None):
    return props.get((tag, attr), (default, ""))[0]


class _WordStyles:
    def __init__(self, root):
        self.styles = {s.get("styleId"): s for s in root.findall("style")}
        self.defaults = {s.get("type"): k for k, s in self.styles.items() if s.get("default") in ("1", "true")}
        self.base_p = _wprops(root.find("docDefaults/pPrDefault/pPr"), "docDefaults")
        self.base_r = _wprops(root.find("docDefaults/rPrDefault/rPr"), "docDefaults")
        self.cache = {}

    def resolve(self, key):
        chain = []
        visited = set()
        while key in self.styles and key not in self.cache:
            if key in visited:
                raise ValueError("DOCX 스타일 상속이 순환합니다.")
            visited.add(key)
            chain.append(key)
            key = _attrs(self.styles[key], "basedOn").get("val")
        para, run = self.cache.get(key, ({}, {}))
        for k in reversed(chain):
            style = self.styles[k]
            label = f"style {k}"
            para = _props_merge(para, _wprops(style.find("pPr"), label) | _numpr(style.find("pPr")))
            run = _props_merge(run, _wprops(style.find("rPr"), label))
            self.cache[k] = para, run
        return para, run


def _word_chars(unit, role, props, text, index, theme):
    run = f"run {index}"
    color = _val(props, "color")
    theme_color = _val(props, "color", "themeColor")
    basis = "read"
    if theme_color:
        slot = {"dark1":"dk1", "dark2":"dk2", "light1":"lt1", "light2":"lt2",
                "hyperlink":"hlink", "followedHyperlink":"folHlink"}.get(theme_color, theme_color)
        scheme = theme.find(".//clrScheme")
        entry = next((c for c in scheme if c.tag == slot), None) if scheme is not None else None
        value = _attrs(entry, "srgbClr").get("val") or _attrs(entry, "sysClr").get("lastClr")
        if value and not (_val(props, "color", "themeTint") or _val(props, "color", "themeShade")):
            color = value
        else:
            basis = "confirm"
    if color and re.fullmatch("[a-fA-F0-9]{6}", color):
        source = props["color", "themeColor" if theme_color else "val"][1]
        unit.add(role, "color", "#" + color.upper(), f"{run} · {source} · color {theme_color or ''}", basis)
    for script in _scripts(text):
        size_key = "szCs" if script == "complex" else "sz"
        size = _val(props, size_key)
        if size is not None:
            unit.add(role, "font_pt", _number(size) / 2, f"{run} · {props[size_key, 'val'][1]} · {size_key}")
        fields = {"latin": ("ascii", "asciiTheme", "hAnsi", "hAnsiTheme"),
                  "complex": ("cs", "cstheme", "csTheme")}.get(script, ("eastAsia", "eastAsiaTheme"))
        for field in fields:
            if ("rFonts", field) not in props:
                continue
            name, source = props["rFonts", field]
            if field.lower().endswith("theme"):
                family = "majorFont" if name.startswith("major") else "minorFont"
                face = theme.find(f".//{family}") if theme is not None else None
                tag = {"hangul": "Hang", "hanja": "Hani", "japanese": "Jpan"}.get(script)
                base = _attrs(face, "ea" if tag else "cs" if script == "complex" else "latin").get("typeface")
                name = (_attrs(face, f"font[@script='{tag}']").get("typeface") if tag else None) or base or name
            unit.font(role, name, f"{run} · {source} · rFonts/{field} ({script})")
            break


def _word_indents(unit, role, props, depth, parent_origin):
    value = _val(props, "ind", "start", _val(props, "ind", "left"))
    hanging = _val(props, "ind", "hanging")
    first = _val(props, "ind", "firstLine")
    if role != "heading" and any(tag == "ind" and attr.endswith("Chars") for tag, attr in props):
        for key in ("first_indent_mm", "rest_indent_mm"):
            unit.add(role, key, "", "ind · 문자 단위이므로 지면에서 길이 확인", "confirm")
        return None
    if role == "heading" or all(x is None for x in (value, hanging, first)):
        return None
    left = _number(value or 0) * TWIP_MM
    first = left + (_number(first or 0) - _number(hanging or 0)) * TWIP_MM
    unit.add(role, "first_indent_mm", first, "ind · 첫 줄 시작")
    unit.add(role, "rest_indent_mm", left, "ind · 계속 줄 시작")
    if depth:
        unit.add(role, f"levels.{depth}.indent_mm", first - parent_origin, "ind · 부모 본문 시작 기준",
                 "read" if first >= parent_origin else "confirm")
    return left


def _word_numbering(unit, level, depth):
    prefix = f"levels.{depth}."
    fmt = _attrs(level, "numFmt").get("val")
    pattern = _attrs(level, "lvlText").get("val")
    if pattern is not None:
        token = {"decimal": "{숫자}", "decimalEnclosedCircle": "{원숫자}", "ganada": "{한글}"}.get(fmt)
        expr = _expr(pattern, token, "%" + str(depth))
        unit.add("list", prefix + "expr", expr if expr is not None else pattern,
                 f"numbering · lvl {depth} · {fmt}", "read" if expr is not None else "unsupported")
    suffix = _attrs(level, "suff").get("val", "tab")
    if suffix == "nothing":
        unit.add("list", prefix + "gap_mm", 0, "numbering · suff=nothing")
    elif suffix in ("tab", "space"):
        unit.add("list", prefix + "gap_mm", "", f"numbering · suff={suffix} · 실제 표지 폭 확인", "confirm")


class _WordNumbering:
    def __init__(self, root):
        self.levels = {(a.get("abstractNumId"), level.get("ilvl")): level for a in root.findall("abstractNum")
                       for level in a.findall("lvl")}
        self.nums = {n.get("numId"): n for n in root.findall("num")}

    def level(self, num_id, depth):
        definition = self.nums.get(num_id)
        if definition is None:
            return None
        override = definition.find(f"lvlOverride[@ilvl='{int(depth)}']/lvl")
        if override is not None:
            return override
        abstract = _attrs(definition, "abstractNumId").get("val")
        return self.levels.get((abstract, depth))


def _word_nodes(root, tag):
    # Keep only ancestry on the stack, even for a paragraph with thousands of runs.
    pending = [iter(root)]
    while pending:
        node = next(pending[-1], None)
        if node is None:
            pending.pop()
        elif node.tag == tag:
            yield node
        elif node.tag not in ("drawing", "txbxContent", "pict", "del", "moveFrom", "object"):
            pending.append(iter(node))


def _saved_pages(z, root_rels, paragraphs):
    part = root_rels.get("extended-properties", "docProps/app.xml")
    if part not in z.NameToInfo:
        return {}
    declared = _xml(z, part).findtext("{*}Pages", "")
    if not declared.isascii() or not declared.isdigit():
        return {}
    current = 1
    spans = {}
    for i, p in enumerate(paragraphs):
        first = None
        for run in _word_nodes(p, "r"):
            for child in run:
                if child.tag == "lastRenderedPageBreak":
                    current += 1
                elif child.tag == "t" and (child.text or "").strip():
                    first = first or current
                    spans[i] = (first, current)
    return spans if current > 1 and str(current) == declared else {}


class _RunningArea:
    """A source variant reduced to editable single-line slots and shared typography."""
    def __init__(self, settings):
        self.limit = settings["max_excerpt_chars"]
        self.slots = {}
        self.slot = "left"
        self.styles = {}
        self.review = set()
        self.confirmed_blank = False

    def align(self, align):
        self.slot = {"start": "left", "end": "right"}.get(align, align)
        if self.slot not in SLOTS:
            self.slot = "left"
            self.review.add("정렬 확인")

    def literal(self, text):
        remaining = self.limit - len(self.slots.get(self.slot, ""))
        if len(text) > remaining:
            self.review.add("긴 문구 확인")
            text = text[:remaining]
        if re.search(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]", text):
            self.review.add("여러 줄·간격 확인")
            text = re.sub(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]+", " ", text)
        value = escape_running_literal(text)
        if len(value) > remaining:
            self.review.add("긴 문구 확인")
            # Clip literal characters before escaping so a quoted brace stays whole.
            value = escape_running_literal(text[:remaining // 2])
        self.slots[self.slot] = self.slots.get(self.slot, "") + value

    def page(self):
        if len(self.slots.get(self.slot, "")) + len("{현재쪽}") <= self.limit:
            self.slots[self.slot] = self.slots.get(self.slot, "") + "{현재쪽}"
        else:
            self.review.add("긴 문구 확인")

    def add(self, role, key, value, evidence, basis="read"):
        if value is None:
            return
        prior = self.styles.get(key)
        if prior is None:
            self.styles[key] = (value, evidence, basis)
        elif prior[0] != value:
            self.styles[key] = (prior[0], "영역 안에 여러 글자 서식 · " + key, "confirm")

    font = _Unit.font

    def candidates(self):
        reason = " · ".join(sorted(self.review))
        for slot, expr in (self.slots or {self.slot: ""}).items():
            yield slot, expr, reason or "원문·쪽 번호 제어", "confirm" if reason else "read"
        for key, (value, evidence, basis) in self.styles.items():
            yield key, value, evidence, basis


@dataclass
class _AreaSource:
    area: _RunningArea | None  # None denotes an active face known to be blank.
    role: str
    variant: str
    face: str | None
    group: str
    source: str
    offset: float | None

    def attach(self, unit):
        meta = dict(source_group=self.group, variant=self.variant, suggested_face=self.face)
        if self.area is None:
            unit.add(self.role, "visible", False, f"{self.source} · 빈 영역", **meta)
            return
        for key, value, evidence, basis in self.area.candidates():
            unit.add(self.role, key, value, f"{self.source} · {evidence}",
                     basis if self.face else "confirm", **meta)
        if self.offset is not None:
            unit.add(self.role, "offset_mm", self.offset, f"{self.source} · 종이 끝 거리",
                     "read" if self.face else "confirm", **meta)


def _finish_section(units, section_start, section, part, areas, settings, warnings):
    if areas and len(units) == section_start:
        unit = _Unit(f"구역 {section} · 머리글·바닥글", "표지" if section == 1 else "본문",
                     "", part, settings, warnings)
        for area in areas:
            area.attach(unit)
        units.append(unit.data)
    if len(units) > settings["max_units"]:
        raise ValueError("참고 문서의 분석 단위 수가 한도를 넘었습니다.")


def _events(root, skip):
    """Visit both boundaries of nested simple fields, without entering embedded objects."""
    stack = [(root, iter(root))]
    while stack:
        parent, children = stack[-1]
        child = next(children, None)
        if child is None:
            stack.pop()
            yield parent, False
        else:
            yield child, True
            if child.tag not in skip:
                stack.append((child, iter(child)))


def _word_area(root, role, styles, theme, settings):
    area = _RunningArea(settings)
    area.confirmed_blank = True
    paragraphs = 0
    fields = []
    active = True
    run_index = 0
    tabs = iter(())
    pstyle = {}
    rprops = {}

    def separate():
        nonlocal active
        frame = fields[-1]
        if frame["separate"]:
            return
        instruction = frame["instruction"].strip().upper().split()
        name = instruction[0] if instruction else ""
        if frame["outer"]:
            if name == "PAGE":
                area.page()
                if instruction[1:] not in ([], [r"\*", "MERGEFORMAT"], [r"\*", "ARABIC"]):
                    area.review.add("PAGE 표시 형식 확인")
            else:
                area.review.add("NUMPAGES 전체 쪽 수 확인" if name == "NUMPAGES" else "지원되지 않는 필드 확인")
        frame["separate"] = True
        active = frame["outer"] and name != "PAGE"

    skip = {"drawing", "pict", "object", "del", "moveFrom", "pPr", "rPr"}
    blank_markup = {"p", "r", "pPr", "rPr", "del", "moveFrom", "bookmarkStart", "bookmarkEnd", "proofErr"}
    for node, entering in _events(root, skip):
        tag = node.tag
        if not entering:
            if tag == "fldSimple" and fields:
                active = fields.pop()["outer"]
            continue
        if tag not in blank_markup and (tag != "t" or (node.text or "").strip()):
            area.confirmed_blank = False
        if tag == "p":
            paragraphs += 1
            if paragraphs > settings["max_units"]:
                raise ValueError("머리글·바닥글 문단 수가 분석 한도를 넘었습니다.")
            sid = _attrs(node, "pPr/pStyle").get("val", styles.defaults.get("paragraph"))
            pp, rp = styles.resolve(sid)
            pstyle = _props_merge(styles.base_r, rp)
            para = _props_merge(_props_merge(styles.base_p, pp), _wprops(node.find("pPr"), "pPr 직접 지정"))
            if any(key in ("pBdr", "shd") and paint for (key, _), (paint, _) in para.items()):
                area.confirmed_blank = False
                area.review.add("문단 테두리·음영 확인")
            area.align(_val(para, "jc", default="left"))
            tabs = iter(t.get("val") for t in node.findall("pPr/tabs/tab") if t.get("val") != "clear")
            if paragraphs > 1:
                area.review.add("여러 문단 한 줄로 조정")
                if area.slots.get(area.slot):
                    area.literal(" ")
        elif tag == "r":
            run_index += 1
            rid = _attrs(node, "rPr/rStyle").get("val", styles.defaults.get("character"))
            rprops = _props_merge(_props_merge(pstyle, styles.resolve(rid)[1]), _wprops(node.find("rPr"), "rPr"))
        elif tag == "fldSimple" or (tag == "fldChar" and node.get("fldCharType") == "begin"):
            fields.append(dict(outer=active, separate=False, instruction=node.get("instr", "")[:area.limit]))
            active = False
            if tag == "fldSimple":
                separate()
        elif tag == "instrText" and fields and not fields[-1]["separate"]:
            frame = fields[-1]
            frame["instruction"] += (node.text or "")[:max(0, area.limit - len(frame["instruction"]))]
        elif tag == "fldChar" and fields:
            if node.get("fldCharType") in ("separate", "end"):
                separate()
            if node.get("fldCharType") == "end":
                active = fields.pop()["outer"]
        elif tag in ("t", "noBreakHyphen"):
            text = "\u2011" if tag == "noBreakHyphen" else node.text or ""
            if active:
                area.literal(text)
            if text.strip() and (active or (fields and fields[-1]["separate"] and fields[-1]["outer"])):
                _word_chars(area, role, rprops, text, run_index, theme)
        elif active and tag in ("ptab", "tab"):
            if tag == "ptab":
                align = node.get("alignment")
            else:
                align = next(tabs, None)
            if align not in SLOTS:
                area.review.add("탭 위치 확인")
                align = "center" if area.slot == "left" else "right"
            area.align(align)
        elif active and tag in ("tbl", "drawing", "pict", "object", "br", "cr"):
            area.review.add("표·그림·여러 줄 배치 확인")
            if tag in ("br", "cr"):
                area.literal(" ")
    if fields:
        area.review.add("필드 경계 확인")
    if not area.confirmed_blank and not any(s.strip() for s in area.slots.values()):
        area.review.add("문구로 읽을 수 없는 내용 확인")
    return area


def _word_switch(parent, name):
    node = parent.find(name) if parent is not None else None
    if node is None:
        return False
    val = node.get("val", "true").lower()
    if val in ("1", "true", "on"):
        return True
    if val in ("0", "false", "off"):
        return False
    return None


def _optional_word_xml(z, path, tag):
    if path not in z.NameToInfo:
        return None
    try:
        root = _xml(z, path)
    except (ET.ParseError, BadZipFile, CompressionError, LookupError, NotImplementedError):
        return None
    return root if root.tag == tag else None


def _word_sources(parts, sect, section, parity):
    first = _word_switch(sect, "titlePg")
    faces = {"first": "first" if first else None, "even": "even" if parity else None,
             "default": None if parity is None else "odd" if parity else "default"}
    effective = parts.copy()
    for role in ("header", "footer"):
        for variant in ("first", "even"):
            if faces[variant]:
                effective.setdefault((role, variant), (None, "이전 구역에서 상속할 파트 없음", section))
    areas = []
    for (role, variant), (area, source, origin) in effective.items():
        face = faces.get(variant)
        if origin != section:
            source = f"구역 {origin}에서 상속 · {source}"
        if face is None:
            enabled = {"first": first, "even": parity}.get(variant)
            source = ("원본 면 비활성" if enabled is False else "면 설정 확인") + " · " + source
        elif variant in ("first", "even") and area is not None and area.confirmed_blank:
            area = None
        if area is not None and not area.confirmed_blank and not any(s.strip() for s in area.slots.values()):
            face = None
        distance = _attrs(sect, "pgMar").get(role)
        offset = _number(distance) * TWIP_MM if distance is not None else None
        areas.append(_AreaSource(area, role, variant, face, f"docx/{section}/{role}/{variant}", source, offset))
    return areas


def _word(z, settings, warnings):
    root_rels = _rels(z, "")
    main = root_rels.get("officeDocument", "word/document.xml")
    doc = _xml(z, main)
    body = doc.find("body")
    if doc.tag != "document" or body is None:
        raise ValueError("DOCX 본문을 찾을 수 없습니다.")
    relationships = _relationships(z, main)
    refs = dict(relationships.values())
    parity = False
    if "settings" in refs:
        configuration = _optional_word_xml(z, refs["settings"], "settings")
        parity = _word_switch(configuration, "evenAndOddHeaders") if configuration is not None else None
        if parity is None:
            warnings.add("DOCX 홀짝 면 설정을 읽지 못해 적용 면 확인이 필요합니다.")
    def part(kind, default):
        path = refs.get(kind, posixpath.join(posixpath.dirname(main), default))
        return _xml(z, path) if path in z.NameToInfo else ET.Element(kind)
    styles = _WordStyles(part("styles", "styles.xml"))
    numbering = _WordNumbering(part("numbering", "numbering.xml"))
    theme = part("theme", "theme/theme1.xml")
    # Section properties on a paragraph apply backwards to the section ending there.
    max_units = settings["max_units"]
    paragraphs = list(islice(_word_nodes(body, "p"), max_units + 1))
    if len(paragraphs) > max_units:
        raise ValueError("참고 문서의 문단 수가 분석 한도를 넘었습니다.")
    pages = _saved_pages(z, root_rels, paragraphs)
    ends = [(i, sect) for i, p in enumerate(paragraphs) if (sect := p.find("pPr/sectPr")) is not None]
    last = len(paragraphs) - 1
    if not ends or ends[-1][0] < last or body.find("sectPr") is not None:
        ends.append((last, body.find("sectPr")))
    units = []
    origins = {}
    start = 0
    running_parts = {}
    running_cache = {}
    for section_no, (end, sect) in enumerate(ends, 1):
        section_start = len(units)
        if sect is not None:
            for role in ("header", "footer"):
                for ref in sect.iterfind(role + "Reference"):
                    variant = ref.get("type", "default")
                    if variant not in ("default", "first", "even", "odd"):
                        variant = "unknown"
                    kind, path = relationships.get(ref.get("id"), (None, None))
                    if kind != role:
                        path = None
                    if (role, path) not in running_cache:
                        root = _optional_word_xml(z, path, "hdr" if role == "header" else "ftr")
                        if root is None:
                            area = _RunningArea(settings)
                            area.review.add("파트 판독 실패 · 원본 확인")
                            warnings.add("DOCX 머리글·바닥글의 로컬 파트를 확인할 수 없습니다.")
                        else:
                            area = _word_area(root, role, styles, theme, settings)
                        running_cache[role, path] = area
                    running_parts[role, variant] = (running_cache[role, path], path or "로컬 파트 참조 없음", section_no)
        areas = _word_sources(running_parts, sect, section_no, parity)
        for i in range(start, end + 1):
            p = paragraphs[i]
            ppr = p.find("pPr")
            sid = _attrs(ppr, "pStyle").get("val", styles.defaults.get("paragraph"))
            pstyle, rstyle = styles.resolve(sid)
            direct = _wprops(ppr, "pPr 직접 지정") | _numpr(ppr)
            para = _props_merge(styles.base_p, pstyle)
            num = para | direct
            nid = _val(num, "numId")
            ilvl = _val(num, "ilvl", default="0")
            depth = int(ilvl) + 1 if nid and nid != "0" else 0
            level = None
            if depth:
                level = numbering.level(nid, ilvl)
                if level is not None:
                    para = _props_merge(para, _wprops(level.find("pPr"), "numbering"))
            para = _props_merge(para, direct)
            style_name = _attrs(styles.styles.get(sid), "name").get("val", sid or "")
            heading = (re.match(r"heading|title$|제목|표제|개요", style_name, re.I)
                       or int(_val(para, "outlineLvl", default="9")) < 9)
            role = "list" if depth else "heading" if heading else "body"
            excerpt = _excerpt(_word_nodes(p, "r"), settings["max_excerpt_chars"], warnings)
            if not excerpt.strip():
                continue
            loc = f"구역 {section_no} · 문단 {i - start + 1}"
            if i in pages:
                a, b = pages[i]
                loc += f" · 저장 지면 {a}쪽" if a == b else f" · 저장 지면 {a}–{b}쪽"
            previous = units[-1]["section"] if units and i != start else None
            unit = _Unit(loc, _section(style_name, excerpt, role, not units, previous), excerpt, main, settings, warnings)
            if sect is not None:
                unit.add("document", "page_mm", _dimensions(sect.find("pgSz"), ("w", "h"), TWIP_MM), "sectPr/pgSz")
                unit.add("document", "margins_mm", _dimensions(sect.find("pgMar"), ("top","right","bottom","left"), TWIP_MM), "sectPr/pgMar")
            for area in areas:
                area.attach(unit)
            origin = _word_indents(unit, role, para, depth, origins.get((nid, depth - 1), 0))
            if depth and origin is not None:
                origins[nid, depth] = origin
            if level is not None:
                _word_numbering(unit, level, depth)
            base_run = _props_merge(styles.base_r, rstyle)
            spacing = _val(para, "spacing", "line")
            rule = _val(para, "spacing", "lineRule", "auto")
            for j, r in enumerate(_word_nodes(p, "r"), 1):
                if unit.full():
                    break
                text = "".join(t.text or "" for t in r.iterfind("t"))
                if not text.strip():
                    continue
                rpr = r.find("rPr")
                char_style = _attrs(rpr, "rStyle").get("val", styles.defaults.get("character"))
                props = _props_merge(base_run, styles.resolve(char_style)[1])
                props = _props_merge(props, _wprops(rpr, "rPr 직접 지정"))
                _word_chars(unit, role, props, text, j, theme)
                if spacing is not None:
                    if rule in ("exact", "atLeast"):
                        unit.add(role, "leading_pt", _number(spacing) / 20, "spacing/" + rule,
                                 "read" if rule == "exact" else "confirm")
                    elif _val(props, "sz") is not None:
                        unit.add(role, "leading_pt", _number(spacing) / 240 * _number(_val(props,"sz")) / 2,
                                 "spacing/auto · 글자 크기 기준 배수 추정", "estimated")
            units.append(unit.data)
        _finish_section(units, section_start, section_no, main, areas, settings, warnings)
        start = end + 1
    return units


def _hwp_parts(z):
    opf = _xml(z, "Contents/content.hpf")
    manifest = {item.get("id"): item.get("href", "") for item in opf.findall("manifest/item")}
    def path(ref):
        target = manifest.get(ref)
        if not target:
            raise ValueError("HWPX 구역의 파일 참조가 올바르지 않습니다.")
        return target if target in z.NameToInfo else posixpath.normpath(posixpath.join("Contents", target))
    header = next((path(k) for k, href in manifest.items() if k == "header" or href.endswith("header.xml")), None)
    if header is None:
        raise ValueError("HWPX 서식 정의를 찾을 수 없습니다.")
    sections = [path(item.get("idref")) for item in opf.findall("spine/itemref")]
    if len(set(sections)) != len(sections):
        raise ValueError("HWPX 구역 파일이 중복되었습니다.")
    return _xml(z, header), [s for s in sections if s != header]


def _hwp_chars(unit, role, char, text, idx, fonts):
    label = f"run {idx} · charPr {char.get('id')}"
    refs = _attrs(char, "fontRef")
    relative = _attrs(char, "relSz")
    size = char.get("height")
    for language in _scripts(text):
        slot = "other" if language == "complex" else language
        if size is not None:
            unit.add(role, "font_pt", _number(size) / 100 * _number(relative.get(slot, 100)) / 100,
                     label + " · height")
        name = fonts.get((slot, refs.get(slot)))
        unit.font(role, name, label + f" · fontRef/{slot}")
    color = char.get("textColor", "")
    if re.fullmatch("#[0-9a-fA-F]{6}", color):
        unit.add(role, "color", color.upper(), label + " · textColor")


def _hwp_paragraphs(root):
    pending = [iter(root)]
    while pending:
        node = next(pending[-1], None)
        if node is None:
            pending.pop()
            continue
        if node.tag in ("header", "footer", "footNote", "endNote", "masterPage"):
            continue
        if node.tag == "p":
            yield node
        pending.append(iter(node))


def _hwp_paragraph_format(unit, role, pp, depth, parent_origin):
    if pp is None:
        return
    # Hancom writes modern metrics in the HwpUnitChar case and a legacy alternative.
    branch = next((case for case in pp.findall("switch/case")
                   if "HwpUnitChar" in case.get("required-namespace", "")), None)
    margin = (branch if branch is not None else pp).find("margin")
    label = f"paraPr {pp.get('id')}"
    if margin is not None and role != "heading":
        left = _attrs(margin, "left")
        indent = _attrs(margin, "intent") or _attrs(margin, "indent")
        if (left or indent) and all(a.get("unit", "HWPUNIT") == "HWPUNIT" for a in (left, indent)):
            origin = _number(left.get("value", 0)) * HWP_MM
            first = origin + _number(indent.get("value", 0)) * HWP_MM
            unit.add(role, "first_indent_mm", first, label + " · margin/intent")
            unit.add(role, "rest_indent_mm", origin, label + " · margin/left")
            if depth:
                unit.add(role, f"levels.{depth}.indent_mm", first - parent_origin,
                         label + " · 부모 본문 시작 기준", "read" if first >= parent_origin else "confirm")
            return origin


def _hwp_numbering(unit, heading, depth, numbers, bullets):
    ref = heading.get("idRef")
    if heading.get("type") == "BULLET":
        bullet = bullets.get(ref)
        if bullet is not None:
            symbol = bullet.get("char")
            if symbol:
                unit.add("list", f"levels.{depth}.expr", symbol.replace("{","{{").replace("}","}}"), f"bullet {ref}")
            level = bullet.find("paraHead")
        else:
            level = None
    else:
        level = numbers.get((ref, str(depth)))
        if level is not None and level.text:
            fmt = level.get("numFormat")
            token = {"DIGIT": "{숫자}", "HANGUL_SYLLABLE": "{한글}", "CIRCLED_DIGIT": "{원숫자}"}.get(fmt)
            expr = _expr(level.text, token, "^" + str(depth))
            unit.add("list", f"levels.{depth}.expr", expr if expr is not None else level.text,
                     f"numbering {ref} · paraHead {depth}", "read" if expr is not None else "unsupported")
    if level is not None and level.get("textOffsetType") == "HWPUNIT" and level.get("textOffset") is not None:
        unit.add("list", f"levels.{depth}.gap_mm", _number(level.get("textOffset")) * HWP_MM, "paraHead/textOffset")
    elif level is not None and level.get("textOffsetType", "PERCENT") == "PERCENT" and level.get("textOffset"):
        unit.add("list", f"levels.{depth}.gap_mm", "", f"paraHead/textOffset {level.get('textOffset')}% · 실제 표지 폭 확인", "confirm")


def _hwp_area(control, role, styles, paras, chars, fonts, settings):
    area = _RunningArea(settings)
    if any(n.tag in ("tbl", "pic", "container", "ole", "shape") for n in control.iter()):
        area.review.add("표·그림 배치 확인")
    for i, p in enumerate(control.iter("p")):
        if i >= settings["max_units"]:
            raise ValueError("머리글·바닥글 문단 수가 분석 한도를 넘었습니다.")
        style = styles.get(p.get("styleIDRef", "0"))
        defaults = style.attrib if style is not None else {}
        props = paras.get(p.get("paraPrIDRef", defaults.get("paraPrIDRef")))
        area.align(_attrs(props, "align").get("horizontal", "LEFT").lower())
        if i:
            area.review.add("여러 문단 한 줄로 조정")
            if area.slots.get(area.slot):
                area.literal(" ")
        for j, run in enumerate(p.iterfind("run"), 1):
            char = chars.get(run.get("charPrIDRef", defaults.get("charPrIDRef")))
            for node in run:
                if node.tag == "t":
                    area.literal(node.text or "")
                    for item in node:
                        if item.tag in ("lineBreak", "tab"):
                            area.review.add("줄·탭 위치 확인")
                            area.literal(" ")
                        area.literal(item.tail or "")
                    if char is not None and (node.text or "").strip():
                        _hwp_chars(area, role, char, node.text, j, fonts)
                elif node.tag == "ctrl":
                    for item in node:
                        if item.tag == "autoNum" and item.get("numType") == "PAGE":
                            formatting = _attrs(item, "autoNumFormat")
                            if formatting.get("type", "DIGIT") != "DIGIT":
                                area.review.add("자동 쪽 번호 표시 형식 확인")
                            area.literal(formatting.get("prefixChar", ""))
                            area.page()
                            area.literal(formatting.get("suffixChar", ""))
                            if char is not None:
                                _hwp_chars(area, role, char, "1", j, fonts)
                        else:
                            area.review.add("지원되지 않는 제어 확인")
    return area


def _hwp_running(root, styles, paras, chars, fonts, settings, warnings):
    areas = []
    for n in root.iter():
        if n.tag not in ("header", "footer", "pageNum"):
            continue
        if len(areas) >= settings["max_candidates_per_unit"]:
            warnings.add("구역 내 머리글·바닥글 후보 한도에 도달했습니다.")
            break
        variant = {"BOTH": "default", "ODD": "odd", "EVEN": "even", "FIRST": "first"}.get(
            n.get("applyPageType", "BOTH"), "unknown")
        if n.tag == "pageNum":
            pos = n.get("pos", "").upper().split("_")
            if pos == ["NONE"]:
                continue
            if len(pos) != 2 or sum(part in ("TOP", "BOTTOM") for part in pos) != 1:
                warnings.add("쪽 번호의 지정 위치를 확인할 수 없습니다.")
                continue
            role = "header" if "TOP" in pos else "footer"
            align = next(v.lower() for v in pos if v not in ("TOP", "BOTTOM"))
            if align in ("in", "inside", "inner", "out", "outside", "outer"):
                odd = "left" if align in ("in", "inside", "inner") else "right"
                positions = [("odd", odd), ("even", "right" if odd == "left" else "left")]
            else:
                positions = [(variant, align)]
            for var, slot in positions:
                area = _RunningArea(settings)
                area.align(slot)
                if n.get("formatType", "DIGIT") != "DIGIT":
                    area.review.add("쪽 번호 표시 형식 확인")
                side = n.get("sideChar", "").strip()
                area.literal(side + " " if side else "")
                area.page()
                area.literal(" " + side if side else "")
                areas.append((role, var, area, "pageNum " + n.get("pos", "")))
        else:
            area = _hwp_area(n, n.tag, styles, paras, chars, fonts, settings)
            areas.append((n.tag, variant, area, n.tag + "/subList"))
    return areas


def _hwpx(z, settings, warnings):
    head, parts = _hwp_parts(z)
    if head.tag != "head" or not parts:
        raise ValueError("HWPX 문서의 서식 또는 구역을 찾을 수 없습니다.")
    def indexed(path):
        return {e.get("id"): e for e in head.findall("refList/" + path)}
    styles = indexed("styles/style")
    chars = indexed("charProperties/charPr")
    paras = indexed("paraProperties/paraPr")
    bullets = indexed("bullets/bullet")
    numbers = {(n.get("id"), lvl.get("level")): lvl for n in head.findall("refList/numberings/numbering")
               for lvl in n.findall("paraHead")}
    fonts = {(face.get("lang", "").lower(), font.get("id")): font.get("face")
             for face in head.findall("refList/fontfaces/fontface") for font in face.findall("font")}
    units = []
    origins = {}
    count = 0
    for sec, part in enumerate(parts, 1):
        section_start = len(units)
        root = _xml(z, part)
        if root.tag != "sec":
            raise ValueError("HWPX 구역의 XML 형식이 올바르지 않습니다.")
        page = root.find(".//secPr/pagePr")
        controls = _hwp_running(root, styles, paras, chars, fonts, settings, warnings)
        areas = []
        for index, (band, variant, area, origin) in enumerate(controls):
            margin = _attrs(page, "margin").get(band)
            offset = _number(margin) * HWP_MM if margin is not None else None
            face = variant if variant != "unknown" else None
            areas.append(_AreaSource(area, band, variant, face, f"hwpx/{sec}/{band}/{variant}/{index}",
                                     f"{part} · {origin}", offset))
        for i, p in enumerate(_hwp_paragraphs(root), 1):
            count += 1
            if count > settings["max_units"]:
                raise ValueError("참고 문서의 문단 수가 분석 한도를 넘었습니다.")
            excerpt = _excerpt(p.iterfind("run"), settings["max_excerpt_chars"], warnings)
            if not excerpt.strip():
                continue
            style = styles.get(p.get("styleIDRef", "0"))
            default = style.attrib if style is not None else {}
            pp = paras.get(p.get("paraPrIDRef", default.get("paraPrIDRef")))
            heading = _attrs(pp, "heading")
            depth = int(heading.get("level", 0)) + 1 if heading.get("type") in ("NUMBER", "BULLET") else 0
            name = default.get("name", default.get("engName", ""))
            is_heading = heading.get("type") == "OUTLINE" or re.match("제목|표제|개요|heading|title$", name, re.I)
            role = "list" if depth else "heading" if is_heading else "body"
            previous = units[-1]["section"] if units and i > 1 else None
            unit = _Unit(f"구역 {sec} · 문단 {i}", _section(name, excerpt, role, not units, previous),
                         excerpt, part, settings, warnings)
            unit.add("document", "page_mm", _dimensions(page, ("width","height"), HWP_MM), "pagePr")
            if page is not None:
                unit.add("document", "margins_mm", _dimensions(page.find("margin"), ("top","right","bottom","left"), HWP_MM),
                         "pagePr/margin")
            for area in areas:
                area.attach(unit)
            origin = _hwp_paragraph_format(unit, role, pp, depth, origins.get((heading.get("idRef"), depth-1), 0))
            if depth:
                if origin is not None:
                    origins[heading.get("idRef"), depth] = origin
                _hwp_numbering(unit, heading, depth, numbers, bullets)
            spacing = _attrs(pp, ".//lineSpacing")
            for j, r in enumerate(p.iterfind("run"), 1):
                if unit.full():
                    break
                text = "".join(chunk for t in r.iterfind("t") for chunk in t.itertext())
                if not text.strip():
                    continue
                char = chars.get(r.get("charPrIDRef", default.get("charPrIDRef")))
                if char is not None:
                    _hwp_chars(unit, role, char, text, j, fonts)
                    if spacing.get("type") == "PERCENT" and char.get("height") and spacing.get("value"):
                        leading = _number(char.get("height")) * _number(spacing["value"]) / 10000
                        unit.add(role, "leading_pt", leading, "lineSpacing/PERCENT · 글자 크기 기준 추정", "estimated")
            if spacing.get("type") in ("FIXED", "AT_LEAST") and spacing.get("value"):
                unit.add(role, "leading_pt", _number(spacing["value"]) / 100, "lineSpacing/" + spacing["type"],
                         "read" if spacing["type"] == "FIXED" else "confirm")
            units.append(unit.data)
        _finish_section(units, section_start, sec, part, areas, settings, warnings)
    return units


def analyze_office(data, fmt, settings):
    """Return ordered paragraph evidence for local DOCX and HWPX packages."""
    if len(data) > settings["max_bytes"]:
        raise ValueError("참고 파일 크기가 한도를 넘었습니다.")
    warnings = set()
    if not {"max_candidates_per_unit", "max_excerpt_chars", "max_evidence_chars"} <= settings.keys():
        settings = read_config(CONFIG_DIR / "reference.json") | settings
    fmt = fmt.lower().lstrip(".")
    try:
        with ZipFile(BytesIO(data)) as z:
            entries = z.infolist()
            if len(entries) > settings["max_entries"] or sum(x.file_size for x in entries) > settings["max_uncompressed_bytes"]:
                raise ValueError("참고 파일의 압축 해제 한도를 넘었습니다.")
            if len(z.NameToInfo) != len(entries) or any(x.flag_bits & 1 for x in entries):
                raise ValueError("중복 경로나 암호가 있는 참고 파일은 읽을 수 없습니다.")
            if fmt == "docx":
                units = _word(z, settings, warnings)
                thumbnail = _rels(z, "").get("thumbnail", "")
            elif fmt == "hwpx":
                units = _hwpx(z, settings, warnings)
                thumbnail = "Preview/PrvImage.png"
            else:
                raise ValueError("지원하지 않는 참고 문서 형식입니다.")
            if units and thumbnail in z.NameToInfo and {"max_pixels", "raster_width"} <= settings.keys():
                from app.reference_media import _image_preview

                ext = thumbnail.rsplit(".", 1)[-1].lower()
                if ext in ("png", "jpg", "jpeg"):
                    try:
                        units[0]["preview"] = _image_preview(z.read(thumbnail), "png" if ext == "png" else "jpeg", settings)
                    except ValueError:
                        warnings.add("문서의 대표 이미지를 읽을 수 없어 문단 근거만 표시합니다.")
    except (BadZipFile, ET.ParseError, CompressionError, NotImplementedError, OverflowError, LookupError) as exc:
        raise ValueError("참고 파일의 압축 또는 XML 구조가 손상되었습니다.") from exc
    except ValueError as exc:
        if not re.search("[가-힣]", str(exc)):
            raise ValueError("참고 문서의 서식 값이 올바르지 않습니다.") from exc
        raise
    if not units:
        raise ValueError("참고 문서에서 서식을 확인할 문단을 찾지 못했습니다.")
    return {"units": units, "warnings": sorted(warnings)}
