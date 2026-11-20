"""Extract editable style evidence from PDF pages and operator-analyzed scans."""
import base64
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import groupby, islice
import http.client
import io
import json
import math
from pathlib import Path
import re
from statistics import median
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from PIL import Image, ImageOps

from app.configuration import CONFIG_DIR, read_config
from app.list_marks import format_mark
from app.publication import FONT_LABELS
from app.running_heads import RUNNING_ROLES, SLOTS, escape_running_literal, parse_running_text

MM_PER_BP = 25.4 / 72
PT_PER_BP = 72.27 / 72
# Poppler rounds fontspec sizes to integers. Zoom before converting back to PDF points.
ZOOM = 3
_DEFAULTS = read_config(CONFIG_DIR / "reference.json")
COLOR = re.compile(r"#[0-9a-fA-F]{6}")
LEVEL_KEY = re.compile(r"levels\.[1-9]\.(expr|indent_mm|gap_mm)")
MARK = re.compile(r"^(?:(\d+)([.)])|([①-⑳])|([가-하])([.)])|([•●○▪ㅁㅇ-]))(?=\s|$)")
HEADER_END = .12
FOOTER_START = .90
SCAN_PROMPT = """Examine only the supplied reference page as a layout sample.
Return a JSON object with header, footer, section (표지, 목차 or 본문), excerpt and candidates.
Each candidate has role, key, value and evidence (short visible region in Korean).
Estimate lengths as integer ten-thousandths of the full image width; for example a 20px
type size on a 1000px-wide image is 200. Never infer absolute mm or pt.
For heading/body/list use font_pt, leading_pt or color (#RRGGBB); font names only if legible.
For body/list also first_indent_mm/rest_indent_mm. For document, margins_mm is [top,right,bottom,left].
For list, levels.1.expr may use {숫자}, {한글}, {원숫자} or a literal marker.
Inspect the top and bottom margins. In header and footer copy the visible words,
digits and punctuation exactly into left, center, right. Use "" for empty slots.
An isolated number at the bottom belongs in footer. Leave body text out of these slots.
Copy digits literally. Report any unclear or multiline reading in the evidence of a candidate.
For these roles you can also estimate font_pt, color, font and offset_mm (distance
from the paper edge to the nearest edge of the text in the same relative width units).
For headings and body report typography measurements. Only report visible evidence."""


_MODEL_FIELDS = {
    "role": {"enum": ["document", "heading", "body", "list", *RUNNING_ROLES]},
    "key": {"enum": ["font_pt", "leading_pt", "font", "color", "first_indent_mm", "rest_indent_mm",
                     "margins_mm", "offset_mm", *SLOTS,
                     *(f"levels.{n}.{field}" for n in range(1, 10) for field in ("expr", "gap_mm", "indent_mm"))]},
    "value": {"anyOf": [{"type": "number"}, {"type": "string"},
                        {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}]},
    "evidence": {"type": "string"},
}
_MODEL_SLOTS = {"type": "object", "properties": {key: {"type": "string"} for key in SLOTS},
                "required": list(SLOTS), "additionalProperties": False}
_MODEL_SCHEMA = {"type": "object", "additionalProperties": False, "required": [*RUNNING_ROLES, "section", "excerpt", "candidates"],
                 "properties": {"header": _MODEL_SLOTS, "footer": _MODEL_SLOTS,
                                "section": {"enum": ["표지", "목차", "본문"]}, "excerpt": {"type": "string"},
                                "candidates": {"type": "array", "items": {"type": "object",
                                    "properties": _MODEL_FIELDS, "required": list(_MODEL_FIELDS),
                                    "additionalProperties": False}}}}


def _candidate(role, key, value, basis, evidence, **extra):
    return dict(role=role, key=key, value=value, basis=basis, evidence=evidence, **extra)


def _font(family):
    family = re.sub(r"^[A-Z]{6}\+", "", family).strip()[:96]
    face = re.sub(r"[^a-z0-9가-힣]", "", family.lower())
    for name, label in FONT_LABELS.items():
        if face.startswith(name.replace("-", "")) or face.startswith(label):
            return name, True
    return family, False


def _quoted_prefix(text, limit):
    # Each quoted brace must survive as a pair.
    stop = 0
    for char in text[:limit]:
        limit -= 2 if char in "{}" else 1
        if limit < 0:
            break
        stop += 1
    return escape_running_literal(text[:stop])


def _bounded_text(text, number_span, limit):
    """Keep the same literal context around both interpretations of a number."""
    quoted_length = len(text) + text.count("{") + text.count("}")
    if number_span is not None:
        start, end = number_span
        digits = text[start:end]
        budget = limit - max(len("{현재쪽}"), len(digits))
        if budget >= 0:
            after = _quoted_prefix(text[end:end + budget], budget)
            budget -= len(after)
            before = _quoted_prefix(text[max(0, start - budget):start][::-1], budget)[::-1]
            literal = before + digits + after
            clipped = len(literal) < quoted_length
            if clipped and quoted_length <= limit:
                literal = escape_running_literal(text)
            return literal, before + "{현재쪽}" + after, clipped
    literal = _quoted_prefix(text, limit)
    return literal, None, number_span is not None or len(literal) < quoted_length


def _limited_model_candidate(raw, unit, text_limit):
    if not isinstance(raw, dict):
        return None
    role, key, value = (raw.get(n) for n in ("role", "key", "value"))
    if not isinstance(role, str) or not isinstance(key, str):
        return None
    if role not in _MODEL_FIELDS["role"]["enum"]:
        return None
    note = raw.get("evidence")
    if not isinstance(note, str) or not note.strip():
        return None
    evidence = f"{unit['location']} · {note.strip()}"
    level = LEVEL_KEY.fullmatch(key) if role == "list" else None
    extra = {}
    if role in RUNNING_ROLES:
        extra = dict(source_group=f"page:{unit['page']}:{role}", variant="unknown", suggested_face=None)
        evidence = f"{evidence} · 단일 지면, 면 직접 선택"
        if key in SLOTS:
            if not isinstance(value, str) or not value.strip():
                return None
            try:
                parse_running_text(escape_running_literal(value))
            except ValueError:
                return None
            number_span = None
            for number in re.finditer(r"[0-9]+", value):
                number_span = number.span()
            literal, auto, clipped = _bounded_text(value, number_span, text_limit)
            if clipped:
                evidence = "문구 길이 한도 · 원문 확인 · " + evidence
            if auto is not None:
                extra["options"] = [dict(kind="literal", value=literal), dict(kind="page", value=auto)]
            return _candidate(role, key, literal, "confirm", evidence, **extra)
        if key not in ("font", "font_pt", "color", "offset_mm"):
            return None
    if key == "color" and role != "document" and isinstance(value, str) and COLOR.fullmatch(value):
        return _candidate(role, key, value.lower(), "estimated", evidence, **extra)
    if key == "font" and role != "document" and isinstance(value, str) and value.strip():
        family, known = _font(value)
        return _candidate(role, key, family, "estimated" if known else "confirm", evidence, **extra)
    if level and level[1] == "expr" and isinstance(value, str) and len(value) <= 64:
        try:
            format_mark(value, 1, parents=())
        except ValueError:
            return None
        return _candidate(role, key, value, "estimated", evidence)
    if role == "document" and key == "margins_mm":
        if not isinstance(value, list) or len(value) != 4:
            return None
        numbers = value
    elif ((role != "document" and key in ("font_pt", "leading_pt")) or
          (role in RUNNING_ROLES and key == "offset_mm") or
          (role in ("body", "list") and key in ("first_indent_mm", "rest_indent_mm")) or
          (level and level[1] in ("indent_mm", "gap_mm"))):
        numbers = [value]
    else:
        return None
    if any(type(n) not in (float, int) or not 0 <= n <= 10000 for n in numbers):
        return None
    if key in ("font_pt", "leading_pt") and value == 0:
        return None
    return _candidate(role, key, value, "estimated", evidence, unit="width_per_10000", **extra)


def _remaining(deadline):
    left = deadline - monotonic()
    if left <= 0:
        raise TimeoutError
    return left


def _scan_slot(crop):
    """Measure a single narrow line of ink entirely inside one horizontal third."""
    gray = crop.convert("L")
    if sum(gray.histogram()[220:]) < .85 * gray.width * gray.height:
        return None
    mask = gray.point(lambda px: 255 if px < 170 else 0)
    box = mask.getbbox()
    if box:
        x, y, right, bottom = box
        first, last = x * 3 // gray.width, (right - 1) * 3 // gray.width
        if (first == last and 2 <= bottom - y <= gray.height * .6 and right - x >= bottom - y
                and all(mask.getprojection()[1][y:bottom])):
            return SLOTS[first], f"x={x}–{right}px / {gray.width}px"
    return None


def _vision(unit, settings, deadline, hints):
    url = urlsplit(settings["vision_url"])
    if not url.hostname or not settings["vision_model"]:
        raise ValueError("모델 연결 설정 필요")
    deadline = min(deadline, monotonic() + settings["vision_timeout_seconds"])
    cls = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    conn = cls(url.hostname, url.port, timeout=_remaining(deadline))
    headers = {"Content-Type": "application/json"}
    if settings.get("vision_token"):
        headers["Authorization"] = "Bearer " + settings["vision_token"]
    data = {"model": settings["vision_model"], "temperature": 0, "max_tokens": settings["vision_max_tokens"],
            "response_format": {"type": "json_schema", "json_schema": {"name": "page_style", "schema": _MODEL_SCHEMA}},
            "messages": [{"role": "system", "content": SCAN_PROMPT}, {"role": "user", "content": [
                {"type": "text", "text": hints + " Report the visible typography in the specified units."},
                {"type": "image_url", "image_url": {"url": unit["preview"]}}]}]}
    content = data["messages"][-1]["content"]
    measured_slots = {}
    with Image.open(io.BytesIO(base64.b64decode(unit["preview"].partition(",")[2]))) as image:
        for role, top, bottom in (("header", 0, max(1, round(image.height * HEADER_END))),
                                  ("footer", min(image.height - 1, round(image.height * FOOTER_START)), image.height)):
            band = image.crop((0, top, image.width, bottom))
            measured_slots[role] = _scan_slot(band)
            crop = io.BytesIO()
            band.save(crop, "JPEG", quality=90)
            content.extend([{"type": "text", "text": f"This crop is the {role}. Transcribe the visible text literally "
                                                     "into its matching left/center/right slot. An empty slot is an empty string."},
                            {"type": "image_url", "image_url": {"url": _image_url(crop.getvalue())}}])
    try:
        conn.request("POST", url.path.rstrip("/") + "/chat/completions",
                     body=json.dumps(data).encode(), headers=headers)
        if conn.sock:
            conn.sock.settimeout(_remaining(deadline))
        response = conn.getresponse()
        if response.status != 200:
            raise ValueError("모델 연결 실패")
        reply = response.read(settings["max_vision_response_bytes"] + 1)
    finally:
        conn.close()
    if len(reply) > settings["max_vision_response_bytes"]:
        raise ValueError("모델 응답 길이 초과")
    content = json.loads(reply)["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise ValueError("모델 응답 형식 오류")
    content = content.strip()
    if content.startswith("```") and content.endswith("```"):
        content = content.partition("\n")[2][:-3]
    result = json.loads(content)
    if not isinstance(result, dict) or not isinstance(result.get("candidates"), list):
        raise ValueError("모델 응답 형식 오류")
    if result.get("section") in ("표지", "목차", "본문"):
        unit["section"] = result["section"]
    excerpt = result.get("excerpt")
    if isinstance(excerpt, str):
        unit["excerpt"] = excerpt
    count = len(unit["candidates"])
    slots = []
    for role, edge in zip(RUNNING_ROLES, ("상단", "하단")):
        area = result.get(role)
        if isinstance(area, dict):
            for key, side in zip(SLOTS, ("왼쪽", "가운데", "오른쪽")):
                slots.append(dict(role=role, key=key, value=area.get(key), evidence=f"{edge} {side} · 판독 문구 확인"))
    seen = set()
    for raw in slots + result["candidates"]:
        if len(unit["candidates"]) >= settings["max_candidates_per_unit"]:
            break
        candidate = _limited_model_candidate(raw, unit, settings["max_excerpt_chars"])
        if candidate:
            if candidate["role"] in RUNNING_ROLES:
                field = candidate["role"], candidate["key"]
                if field in seen:
                    continue
                seen.add(field)
            unit["candidates"].append(candidate)
    if len(unit["candidates"]) == count:
        raise ValueError("확인할 수 있는 서식 후보가 없습니다.")
    for role, measured in measured_slots.items():
        readings = [c for c in unit["candidates"] if c["role"] == role and c["key"] in SLOTS]
        if measured and len(readings) == 1:
            candidate, = readings
            slot, coordinates = measured
            candidate["key"] = slot
            side = {"left": "왼쪽", "center": "가운데", "right": "오른쪽"}[slot]
            candidate["evidence"] = f"모델 판독 · {candidate['evidence']} · 잉크 {coordinates} · {side}"


def _ink_sizes(preview):
    """Estimate visible letter height only on light pages with repeated text-sized rows."""
    data = base64.b64decode(preview.partition(",")[2])
    with Image.open(io.BytesIO(data)) as image:
        gray = image.convert("L")
    w, h = gray.size
    if sum(gray.histogram()[220:]) < .7 * w * h:
        return w, {}
    mask = gray.point(lambda p: 255 if p < 170 else 0)
    density = mask.resize((1, h), Image.Resampling.BOX).tobytes()
    bands = []
    for ink, ys in groupby(range(h), key=lambda y: density[y] >= 2):
        if not ink:
            continue
        ys = list(ys)
        band = mask.crop((0, ys[0], w, ys[-1] + 1))
        box = band.getbbox()
        if not box or band.height < 3 or box[2] - box[0] < band.height:
            continue
        # Solid rules, photos and full-height illustrations are poor font rulers.
        area = band.height * (box[2] - box[0])
        if band.height > h * .15 or band.histogram()[255] > .75 * area:
            return w, {}
        bands.append(band.height)
    if len(bands) < 3:
        return w, {}
    typical = median(bands)
    body = [v for v in bands if abs(v - typical) <= max(1, typical * .18)]
    if len(body) < max(3, len(bands) * .6):
        return w, {}
    height = median(body)
    sizes = {"body": (height, len(body))}
    if 1.25 * height <= bands[0] <= 3 * height:
        sizes["heading"] = (bands[0], 1)
    return w, sizes


def _scan(unit, settings, deadline, warnings):
    try:
        _remaining(deadline)
        width, measurements = _ink_sizes(unit["preview"])
        hints = f"Image width {width}px."
        for role, (px, _) in measurements.items():
            hints += f" Measured {role} letter height {px:g}px, equivalent to {round(px * 10000 / width)} width units."
        _vision(unit, settings, deadline, hints)
    except (ValueError, OSError, http.client.HTTPException, KeyError, IndexError, TypeError, RecursionError):
        warnings.append(f"{unit['location']}의 이미지 서식 분석을 완료하지 못했습니다. 이 쪽을 다시 시도하세요.")
        return
    candidates = unit["candidates"]
    # Use the model for page interpretation, and retain reproducible geometry for the estimate.
    candidates[:] = [c for c in candidates if c["key"] != "font_pt" or c["role"] not in measurements]
    for c in candidates:
        if c["key"] == "font_pt":
            c.update(basis="confirm", evidence="글자 높이 확인 필요 · " + c["evidence"])
    for role, (height, lines) in reversed(list(measurements.items())):
        evidence = f"{unit['location']} · {lines}줄 글자 잉크 높이 {height:g}px / 지면 폭 {width}px · 글자 크기 추정"
        candidates.insert(0, _candidate(role, "font_pt", round(10000 * height / width),
                                        "estimated", evidence, unit="width_per_10000"))


def _image_url(data):
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def _image_preview(data, fmt, settings):
    try:
        with Image.open(io.BytesIO(data), formats=[fmt.upper()]) as image:
            if image.width * image.height > settings["max_pixels"]:
                raise ValueError("참고 이미지의 픽셀 수가 한도를 넘습니다.")
            size = settings["raster_width"]
            image.thumbnail((size, size))
            image = ImageOps.exif_transpose(image)
            rgb = Image.new("RGB", image.size, "white")
            if image.has_transparency_data:
                rgba = image.convert("RGBA")
                rgb.paste(rgba, mask=rgba.getchannel("A"))
            else:
                rgb.paste(image.convert("RGB"))
            out = io.BytesIO()
            rgb.save(out, "JPEG", quality=82)
            return _image_url(out.getvalue())
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError("참고 이미지 형식이나 픽셀 수를 확인하세요.") from error


def _run(args, directory, deadline, limit):
    # Direct Poppler output to a worker-owned file, then bound the read into Python.
    output = directory / "command.out"
    try:
        with output.open("wb") as stream:
            subprocess.run(args, cwd=directory, timeout=_remaining(deadline), check=True,
                           stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as error:
        raise TimeoutError from error
    except subprocess.CalledProcessError as error:
        raise ValueError("열 수 없는 PDF입니다. 암호화 여부와 파일을 확인하세요.") from error
    if output.stat().st_size > limit:
        raise ValueError("PDF 분석 자료의 크기가 한도를 넘습니다.")
    return output.read_bytes()


@dataclass
class Line:
    text: str
    left: float
    top: float
    width: float
    height: float
    size: float
    family: str
    color: str


def _lines(page, fonts):
    for font in page.findall("fontspec"):
        fonts[font.get("id")] = font.attrib
    lines = []
    for text in page.findall("text"):
        words = "".join(text.itertext()).strip()
        if not words:
            continue
        font = fonts[text.get("font")]
        x, y, w, h = (float(text.attrib[n]) / ZOOM for n in ("left", "top", "width", "height"))
        lines.append(Line(words, x, y, w, h, float(font["size"]) / ZOOM, font["family"], font["color"]))
    return sorted(lines, key=lambda line: (line.top, line.left))


def _mark(text):
    match = MARK.match(text)
    if not match:
        return None
    if match[1]:
        return "{숫자}" + match[2]
    if match[3]:
        return "{원숫자}"
    if match[4]:
        return "{한글}" + match[5]
    return match[6]


def _edge(line, height):
    if line.top < height * HEADER_END:
        return "header"
    if line.top + line.height > height * FOOTER_START:
        return "footer"
    return None


@dataclass
class _EdgeSample:
    page: int
    index: int
    line: Line
    parts: list[str]
    group: str
    offset: float


def _common_expression(samples):
    """A one-element tuple is literal. Two elements frame a progressing number."""
    first = samples[0].parts
    if any(s.parts[::2] != first[::2] for s in samples):
        return None
    varying = [i for i in range(1, len(first), 2) if any(s.parts[i] != first[i] for s in samples)]
    if not varying:
        return (samples[0].line.text,)
    if len(varying) != 1:
        return None
    i, = varying
    if any(len(s.parts[i]) > 10 for s in samples):
        return None
    if len({int(s.parts[i]) - s.page for s in samples}) != 1:
        return None
    return "".join(first[:i]), "".join(first[i + 1:])


def _mirrored_repeats(samples, opposite):
    if not samples or not opposite:
        return False
    parity = samples[0].page % 2
    if any(s.page % 2 != parity for s in samples) or any(s.page % 2 == parity for s in opposite):
        return False
    pages = sorted({s.page for side in (samples, opposite) for s in side})
    switches = sum(a % 2 != b % 2 for a, b in zip(pages, pages[1:]))
    return switches >= 3 and _common_expression(samples) is not None and _common_expression(opposite) is not None


def _edge_variants(samples, mirrors=()):
    """Require repeated, interleaved observations before assigning PDF parity."""
    first = [s for s in samples if s.page == 1]
    later = [s for s in samples if s.page > 1]
    even = [s for s in samples if s.page % 2 == 0]
    odd = [s for s in samples if s.page % 2]
    for opposite in mirrors:
        if _mirrored_repeats(samples, opposite):
            yield "odd" if odd else "even", samples
            return
        if (first or opposite[0].page == 1) and _mirrored_repeats(later, [s for s in opposite if s.page > 1]):
            regular = later
            if first:
                if later[0].page % 2 and _common_expression(samples) is not None:
                    regular = samples
                else:
                    yield "first", first
            yield "odd" if later[0].page % 2 else "even", regular
            return
    if even and odd and _common_expression(samples) is not None:
        yield "default", samples
        return
    for odd_sample in (odd, [s for s in odd if s.page > 1]):
        if min(len({s.page for s in even}), len({s.page for s in odd_sample})) < 2:
            continue
        if max(even[0].page, odd_sample[0].page) > min(even[-1].page, odd_sample[-1].page):
            continue
        a, b = _common_expression(even), _common_expression(odd_sample)
        if a is not None and b is not None and a != b:
            yield "even", even
            yield "odd", odd_sample
            if first and odd_sample != odd:
                yield "first", first
            return
    if first and even and any(s.page > 1 for s in odd) and _common_expression(later) is not None:
        yield "first", first
        yield "default", later
        return
    by_text = defaultdict(list)
    for sample in samples:
        by_text[tuple(sample.parts[::2])].append(sample)
    for match in by_text.values():
        yield "unknown", match


def _pdf_edge_fields(m, role, slot, variant, pattern, repeated, evidence, limit):
    line = m.line
    span = None
    if pattern is not None and len(pattern) == 2:
        span = len(pattern[0]), len(line.text) - len(pattern[1])
    literal, auto, clipped = _bounded_text(line.text, span, limit)
    source = dict(source_group=m.group, variant=variant, suggested_face=None if variant == "unknown" else variant)
    basis = "confirm" if not repeated or pattern is None or clipped else "estimated" if auto else "read"
    if clipped:
        evidence = "문구 길이 한도 · " + evidence
    yield _candidate(role, slot, auto or literal, basis, evidence, **source)
    yield _candidate(role, "offset_mm", round(max(0, m.offset) * MM_PER_BP, 3), "estimated", evidence, **source)
    yield _candidate(role, "font_pt", round(line.size * PT_PER_BP, 3), "read", evidence, **source)
    yield _candidate(role, "color", line.color, "read", evidence, **source)
    font, known = _font(line.family)
    yield _candidate(role, "font", font, "read" if known else "confirm", evidence, **source)


def _running_candidates(text, sizes, requested, limit):
    positions = defaultdict(list)
    for n, lines in text.items():
        width, height = sizes[n]
        counts = Counter()
        for index, line in enumerate(lines):
            role = _edge(line, height)
            if role is None:
                continue
            middle = (line.left + line.width / 2) / width
            slot = "left" if middle < 1/3 else "right" if middle > 2/3 else "center"
            anchor = {"left": line.left / width, "center": middle, "right": (line.left + line.width) / width}[slot]
            offset = line.top if role == "header" else height - line.top - line.height
            counts[role, slot] += 1
            group = f"page:{n}:{role}:{slot}:{counts[role, slot]}"
            # Number width can move an ink edge by a few points at a digit transition.
            clusters = positions[role, slot]
            for x, y, samples in clusters:
                if abs(anchor - x) <= .012 and abs(offset / height - y) <= .008:
                    break
            else:
                samples = []
                clusters.append((anchor, offset / height, samples))
            samples.append(_EdgeSample(n, index, line, re.split(r"([0-9]+)", line.text), group, offset))

    output = defaultdict(dict)
    for (role, slot), clusters in positions.items():
        for x, y, samples in clusters:
            other_slot = {"left": "right", "right": "left"}.get(slot)
            mirrors = (rows for mx, my, rows in positions.get((role, other_slot), ())
                       if abs(y - my) <= .008 and abs(1 - x - mx) <= .08)
            for variant, matches in _edge_variants(samples, mirrors):
                first, last = matches[0], matches[-1]
                repeated = first.page != last.page
                pattern = _common_expression(matches)
                if repeated:
                    evidence = f"{first.page}쪽·{last.page}쪽"
                    if pattern and len(pattern) == 2:
                        a, b = pattern
                        first_digit = first.line.text[len(a):len(first.line.text) - len(b)]
                        last_digit = last.line.text[len(a):len(last.line.text) - len(b)]
                        evidence += f" · {first_digit}→{last_digit}"
                    evidence += f" · {first.line.text[:limit]}"
                else:
                    evidence = f"{first.page}쪽 단독 관측"
                    if variant == "first":
                        evidence += f" · 같은 위치 {samples[1].page}쪽 이후와 다름"
                    evidence += f" · {first.line.text[:limit]}"
                face = {"default": "공통", "first": "첫 쪽", "odd": "홀수", "even": "짝수"}.get(variant)
                evidence = ("면 직접 선택" if face is None else f"{face} 면 추정") + " · " + evidence
                for m in matches:
                    recognized = repeated or variant == "first"
                    candidates = (_pdf_edge_fields(m, role, slot, variant, pattern, repeated, evidence, limit)
                                  if m.page in requested else ())
                    output[m.page][m.index] = recognized, candidates
    return output


def _text_styles(unit, lines, width, height, running, nominal_size, label_boxes=None, *, candidate_limit=None):
    location = unit["location"]
    candidates = unit["candidates"]
    content = []
    for index, line in enumerate(lines):
        if index in running:
            recognized, edge_candidates = running[index]
            remaining = None if candidate_limit is None else max(0, candidate_limit - len(candidates))
            candidates.extend(islice(edge_candidates, remaining))
            if recognized:
                continue
        content.append(line)
    if not content:
        return
    left = min(l.left for l in content)
    mm = lambda bp: round(max(0, bp) * MM_PER_BP, 3)
    margins = [mm(content[0].top), mm(width - max(l.left + l.width for l in content)),
               mm(height - max(l.top + l.height for l in content)), mm(left)]
    candidates.append(_candidate("document", "margins_mm", margins, "estimated", location + " · 글줄 외곽"))
    groups = defaultdict(list)
    for line in content:
        role = "list" if _mark(line.text) else "heading" if line.size > nominal_size * 1.16 else "body"
        groups[role].append(line)
    unit["excerpt"] = " ".join(l.text for l in content)
    if any(re.match(r"^(?:목\s*차|차\s*례|(?:table of )?contents)(?:$|[ (：:])", l.text, re.I)
           for l in content[:3]):
        unit["section"] = "목차"
    elif unit["page"] == 1 and "heading" in groups and len(content) <= 4:
        unit["section"] = "표지"
    for role, rows in groups.items():
        face = Counter((l.size, l.family, l.color) for l in rows).most_common(1)[0][0]
        sample = next(l for l in rows if (l.size, l.family, l.color) == face)
        evidence = f"{location} · 위에서 {mm(sample.top)}mm · {sample.text}"
        font, known = _font(sample.family)
        candidates.extend([_candidate(role, "font_pt", round(sample.size * PT_PER_BP, 3), "read", evidence),
                           _candidate(role, "font", font, "read" if known else "confirm", evidence),
                           _candidate(role, "color", sample.color, "read", evidence)])
        gaps = [b.top - a.top for a, b in zip(rows, rows[1:])
                if a.size * .75 <= b.top - a.top <= a.size * 2.5 and abs(a.size - b.size) < .5]
        if gaps:
            candidates.append(_candidate(role, "leading_pt", round(median(gaps) * PT_PER_BP, 3), "estimated", evidence))
    if groups.get("list"):
        origins = []
        for row in sorted(groups["list"], key=lambda row: row.left):
            if not origins or row.left - origins[-1] >= 2:
                origins.append(row.left)
        parent_text = left
        for depth, origin in enumerate(origins[:9], 1):
            siblings = [r for r in groups["list"] if abs(r.left - origin) < 2]
            row = siblings[0]
            evidence = f"{location} · 목록 {row.text}"
            prefix = f"levels.{depth}."
            candidates.append(_candidate("list", prefix + "expr", _mark(row.text), "estimated", evidence))
            if parent_text is not None and origin >= parent_text:
                candidates.append(_candidate("list", prefix + "indent_mm", mm(origin - parent_text), "estimated", evidence))
            # Box positions distinguish a marker's actual width from its following space.
            measured = [(label_boxes or {}).get((round(r.left), round(r.top))) for r in siblings]
            measured = [box for box in measured if box]
            parent_text = None
            if measured:
                gap = min(body - mark_end for mark_end, body in measured)
                candidates.append(_candidate("list", prefix + "gap_mm", mm(gap), "estimated", evidence))
                parent_text = median(body for _, body in measured)


def _label_boxes(raw, pages):
    result = defaultdict(dict)
    for n, page in zip(pages, ET.fromstring(raw).iterfind(".//{*}page")):
        for line in page.iterfind(".//{*}line"):
            words = line.findall("{*}word")
            if len(words) >= 2 and _mark(words[0].text or ""):
                label, body = words[:2]
                at = (round(float(label.get("xMin"))), round(float(label.get("yMin"))))
                result[n][at] = float(label.get("xMax")), float(body.get("xMin"))
    return result


def _unit(page):
    return {"page": page, "location": f"{page}쪽", "section": "본문", "excerpt": "", "candidates": []}


def _pages(count, settings, warnings):
    requested = settings.get("page")
    if requested is not None:
        if type(requested) is not int or not 1 <= requested <= count:
            raise ValueError(f"요청한 쪽이 참고 파일의 범위(1–{count})를 벗어났습니다.")
        return [requested]
    maximum = min(count, settings["max_units"])
    if maximum < count:
        warnings.append(f"단위 수 한도로 {maximum}쪽까지 분석했습니다.")
    return list(range(1, maximum + 1))


def _pdf(data, settings, deadline, directory, warnings):
    source = directory / "reference.pdf"
    source.write_bytes(data)
    size_limit = settings.get("max_uncompressed_bytes", 64 * 1024 * 1024)
    def command(*args):
        return _run(args, directory, deadline, size_limit)
    info = command("pdfinfo", str(source))
    count = re.search(rb"(?m)^Pages:\s*(\d+)\s*$", info)
    if not count or not 1 <= int(count[1]) <= settings["max_pages"]:
        raise ValueError("PDF 쪽 수를 확인하세요. 분석 한도를 넘을 수 없습니다.")
    pages = _pages(int(count[1]), settings, warnings)
    if not pages:
        return []
    # A retry still needs the original bounded text sample to infer page fields.
    sample_end = min(int(count[1]), settings["max_units"])
    span = ["-f", "1", "-l", str(sample_end)] if pages[0] <= sample_end else ["-f", str(pages[0]), "-l", str(pages[-1])]
    details = command("pdfinfo", *span, str(source)).decode("utf-8", "replace")
    sizes = {int(n): (float(w), float(h)) for n, w, h in
             re.findall(r"(?m)^Page\s+(\d+) size:\s*([\d.]+) x ([\d.]+) pts", details)}
    for n, rotation in re.findall(r"(?m)^Page\s+(\d+) rot:\s*(-?\d+)", details):
        if int(rotation) % 180 and int(n) in sizes:
            sizes[int(n)] = sizes[int(n)][::-1]
    images = command("pdfimages", "-list", *span, str(source)).splitlines()[2:]
    large_scans = set()
    for image in images:
        fields = image.split()
        if len(fields) < 14:
            continue
        n, w, h = int(fields[0]), int(fields[3]), int(fields[4])
        if n in pages and w * h > settings["max_pixels"]:
            raise ValueError(f"{n}쪽 이미지의 픽셀 수가 한도를 넘습니다.")
        xppi, yppi = float(fields[-4]), float(fields[-3])
        if n in sizes and xppi > 0 and yppi > 0:
            if w * h * 72**2 / (xppi * yppi) > math.prod(sizes[n]) * .5:
                large_scans.add(n)
    try:
        xml = ET.fromstring(command("pdftohtml", "-xml", "-i", "-stdout", "-noroundcoord",
                                    "-zoom", str(ZOOM), *span, str(source)))
    except ET.ParseError as error:
        raise ValueError("PDF의 글자 배치 정보를 읽을 수 없습니다.") from error
    fonts, text = {}, {}
    for page in xml.findall("page"):
        n = int(page.attrib["number"])
        width, height = sizes.get(n, (float(page.attrib["width"]) / ZOOM, float(page.attrib["height"]) / ZOOM))
        sizes[n] = width, height
        lines = _lines(page, fonts)
        if n in large_scans and all(_edge(l, height) for l in lines):
            lines = []
        text[n] = lines
    running = _running_candidates(text, sizes, set(pages), settings["max_excerpt_chars"])
    font_counts = Counter(line.size for n, lines in text.items() for i, line in enumerate(lines) if i not in running[n] or not running[n][i][0])
    typical = font_counts.most_common(1)[0][0] if font_counts else 0
    label_boxes = {}
    if any(_mark(line.text) for n in pages for line in text[n]):
        try:
            label_boxes = _label_boxes(command("pdftotext", "-bbox-layout", "-f", str(pages[0]), "-l", str(pages[-1]),
                                               str(source), "-"), pages)
        except ET.ParseError:
            warnings.append("목록 표지의 단어 위치를 읽지 못했습니다.")
    units = []
    for n in pages:
        unit = _unit(n)
        width, height = sizes[n]
        unit["candidates"].append(_candidate("document", "page_mm", [round(x * MM_PER_BP, 3) for x in (width, height)],
                                             "read", unit["location"] + " · PDF 판형"))
        if text[n]:
            _text_styles(unit, text[n], width, height, running[n], typical, label_boxes.get(n),
                         candidate_limit=settings["max_candidates_per_unit"])
        units.append(unit)
    # Finish deterministic extraction first so a slow early scan cannot discard later text pages.
    side = min(settings["raster_width"], math.isqrt(settings["max_pixels"]))
    for unit in units:
        n = unit["page"]
        try:
            image = command("pdftoppm", "-f", str(n), "-l", str(n), "-singlefile", "-scale-to", str(side),
                            "-jpeg", str(source))
            unit["preview"] = _image_url(image)
            if not text[n]:
                _scan(unit, settings, deadline, warnings)
        except (ValueError, TimeoutError):
            warnings.append(f"{n}쪽 지면을 처리하지 못했습니다. 이 쪽을 다시 시도하세요.")
    return units


def analyze_media(data, fmt, settings):
    """Analyze the supplied bytes without modifying or rendering the author's manuscript."""
    settings = {**_DEFAULTS, **settings}
    if not data or len(data) > settings["max_bytes"]:
        raise ValueError("참고 파일의 크기를 확인하세요.")
    # Leave time for serialization and the supervisor to collect partial page results.
    timeout = settings["timeout_seconds"]
    deadline = monotonic() + timeout - min(3, timeout * .1)
    warnings = []
    if fmt == "pdf":
        with TemporaryDirectory(prefix="jjok-reference-") as directory:
            try:
                units = _pdf(data, settings, deadline, Path(directory), warnings)
            except TimeoutError:
                raise ValueError("PDF 분석 시간을 초과했습니다.") from None
    elif fmt in ("png", "jpeg"):
        units = []
        if _pages(1, settings, warnings):
            unit = _unit(1)
            unit["preview"] = _image_preview(data, fmt, settings)
            _scan(unit, settings, deadline, warnings)
            units.append(unit)
    else:
        raise ValueError("지원하지 않는 참고 지면 형식입니다.")
    for unit in units:
        unit["excerpt"] = unit["excerpt"][:settings["max_excerpt_chars"]]
        del unit["candidates"][settings["max_candidates_per_unit"]:]
        for candidate in unit["candidates"]:
            candidate["evidence"] = candidate["evidence"][:settings["max_evidence_chars"]]
    return {"units": units, "warnings": warnings}
