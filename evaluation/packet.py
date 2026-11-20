"""Create local, single-reader packets with a separate operator key."""
import csv
import hashlib
import json
import math
import os
import random
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import unicodedata
from collections import defaultdict
from contextlib import contextmanager
from functools import cache
from io import BytesIO
from itertools import repeat
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

HERE = Path(__file__).resolve().parent
HEADER = (HERE / "reading-sheet.csv").read_text().strip().split(",")
RNG = random.SystemRandom()
STOCK = {"quay": "spacing", "drawer": "model", "platform": "model"}
PAGE = {"page_mm": [148, 210], "margins_mm": [20, 18, 20, 18]}
JUSTIFIED_TRACKING_BOUNDS = (-15, 15)
JUSTIFIED_SPACE_BOUNDS = (70, 130)
PDF_TOLERANCE = .05  # PDF points
BP_PER_SP = 72 / 72.27 / 65536
# Independently measured natural space advances in the two locked regular faces.
BODY_FACES = {"nanum-myeongjo": ("NanumMyeongjo", 307 / 1024),
              "nanum-gothic": ("NanumGothic", 280 / 1000)}
QUAY_SHA256 = "57cdb7b231bebe0086f77f6f2ca8e322b0c1917dd50e082f3197344f4f8e0f87"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def compact(text):
    return "".join(unicodedata.normalize("NFKC", text).split())


def paragraphs(source):
    return re.split(r"\n\s*\n", source.strip().replace("\r\n", "\n"))


def corpus():
    return [{"group": name, "split": split, "source": (HERE / "corpus" / (name + ".md")).read_text("utf-8")}
            for name, split in STOCK.items()]


def verify_split(heldout, development):
    """Group IDs cover related rewrites. Text fingerprints catch relabelled copies."""
    seen = {}
    for i, row in enumerate([*heldout, *development]):
        allowed = {"spacing", "model"} if i < len(heldout) else {"train", "dev"}
        if not isinstance(row, dict) or row.get("split") not in allowed:
            raise ValueError("invalid corpus split")
        if not all(isinstance(row.get(k), str) and row[k].strip() for k in ("group", "source")):
            raise ValueError("missing manuscript group or source")
        group = compact(row["group"]).casefold()
        hashes = {digest(compact(s).encode()) for s in [row["source"], *paragraphs(row["source"])]}
        tokens = {("group", group)} | {("text", h) for h in hashes}
        if any(t in seen and (i < len(heldout) or seen[t] != row["split"]) for t in tokens):
            raise ValueError("corpus overlap: " + row["group"])
        seen.update(dict.fromkeys(tokens, row["split"]))


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf-8")


def read_json(path):
    return json.loads(path.read_text("utf-8"))


def blind_pdf(data):
    """Rebuild the physical pages from lossless 300 dpi pixels, discarding all source objects."""
    reader = PdfReader(BytesIO(data))
    writer = PdfWriter()
    writer.metadata = None
    with tempfile.TemporaryDirectory() as tmp:
        prefix = Path(tmp) / "page"
        subprocess.run(["pdftoppm", "-r", "300", "-png", "-", str(prefix)], input=data,
                       capture_output=True, check=True, timeout=60)
        images = sorted(Path(tmp).glob("page-*.png"), key=lambda p: int(p.stem[5:]))
        if not images or len(images) != len(reader.pages):
            raise ValueError("incomplete PDF raster")
        for src, image in zip(reader.pages, images, strict=True):
            width, height = map(float, (src.mediabox.width, src.mediabox.height))
            page = writer.add_blank_page(width, height)
            with Image.open(image) as bitmap:
                stream = DecodedStreamObject()
                stream.set_data(bitmap.convert("RGB").tobytes())
                stream = stream.flate_encode()
                for name, value in {"Type": "XObject", "Subtype": "Image", "ColorSpace": "DeviceRGB"}.items():
                    stream[NameObject("/" + name)] = NameObject("/" + value)
                for name, value in {"Width": bitmap.width, "Height": bitmap.height, "BitsPerComponent": 8}.items():
                    stream[NameObject("/" + name)] = NumberObject(value)
            page["/Resources"][NameObject("/XObject")] = DictionaryObject({NameObject("/Im"): writer._add_object(stream)})
            drawing = DecodedStreamObject()
            drawing.set_data(f"q {width} 0 0 {height} 0 0 cm /Im Do Q".encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(drawing)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@contextmanager
def new_run(dest, experiment):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise FileExistsError(dest)
    tmp = Path(tempfile.mkdtemp(dir=dest.parent))
    try:
        (tmp / "reader").mkdir()
        (tmp / "private").mkdir(mode=0o700)
        key = {"protocol": 1, "run": secrets.token_hex(12), "experiment": experiment, "groups": {}}
        yield tmp, key
        with (tmp / "reader/reading-sheet.csv").open("w", encoding="utf-8", newline="") as f:
            sheet = csv.DictWriter(f, HEADER)
            sheet.writeheader()
            for gid, group in key["groups"].items():
                for sid in group["samples"]:
                    sheet.writerow({"run": key["run"], "group": gid, "sample": sid})
        save_json(tmp / "private/key.json", key)
        if dest.exists():
            raise FileExistsError(dest)
        os.rename(tmp, dest)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)


def new_group(key, manuscript):
    gid = secrets.token_hex(8)
    group = {"manuscript": manuscript["group"], "source_sha256": digest(manuscript["source"].encode()),
             "paragraphs": len(paragraphs(manuscript["source"])), "samples": {}}
    key["groups"][gid] = group
    return gid, group


def add_pdf(tmp, group, data, evidence):
    sid = secrets.token_hex(8)
    blind = blind_pdf(data)
    (tmp / "private" / (sid + ".pdf")).write_bytes(data)
    (tmp / "reader" / (sid + ".pdf")).write_bytes(blind)
    group["samples"][sid] = {**evidence, "pdf_sha256": digest(data), "blind_sha256": digest(blind)}
    return digest(blind)


def measured(call, *args, **kwargs):
    start = time.perf_counter_ns()
    result = call(*args, **kwargs)
    return result, (time.perf_counter_ns() - start) / 1_000_000


def _word_gaps(chars, offset, word_ends, tracking, spaces, where):
    """Read adjustments in print order. Native Latin kerning is outside both channels."""
    gaps = []
    for a, b in zip(chars, chars[1:]):
        offset += len(a.text)
        gap = b.x0 - a.x0 - a.advance
        if offset in word_ends:
            low, high = spaces
            kind = "space"
        elif "가" <= a.text <= "힣" and "가" <= b.text <= "힣":
            low, high = tracking
            kind = "tracking"
        else:
            if gap >= spaces[0] - PDF_TOLERANCE:
                raise ValueError(f"{where}: unexpected space inside source word at {offset}")
            gaps.append(("native", gap))
            continue
        if not low - PDF_TOLERANCE <= gap <= high + PDF_TOLERANCE:
            raise ValueError(f"{where}: {kind} outside requested bounds at {offset}")
        gaps.append((kind, gap))
    return gaps


def _natural_final(gaps, tracking, space, remaining, where):
    gaps = [(kind, actual) for kind, actual in gaps if kind != "native"]
    if gaps and all(kind == "tracking" for kind, _ in gaps):
        # Reconstruct spare width at the preferred setting using this printed word.
        # A single unbroken word may need just enough compression to fit the line.
        natural_spare = remaining - sum(tracking - actual for _, actual in gaps)
        if natural_spare < 0:
            tracking += natural_spare / len(gaps)
    drift = 0.
    for kind, actual in gaps:
        expected = space if kind == "space" else tracking
        drift += actual - expected
        if max(abs(actual - expected), abs(drift)) > PDF_TOLERANCE:
            raise ValueError(f"{where}: natural final {kind} differs from printed PDF")


def _native_advance(font_id, size):
    """Use the locked face, including its Latin kerning, for replayed glyph origins."""
    record = read_json(HERE.parent / "runtime.lock")["fonts"][font_id]["regular"]
    pinned = Path(record["path"])
    path = Path(os.environ.get("JJOKGYEOL_FONT_DIR", pinned.parent)) / pinned.name
    data = path.read_bytes()
    if digest(data) != record["sha256"]:
        raise ValueError(f"font differs from runtime.lock: {path}")
    with TTFont(BytesIO(data)) as face:
        cmap = face.getBestCmap()
        metrics = face["hmtx"].metrics
        if "kern" in face:
            kern = face["kern"].kernTables[0].kernTable  # Locked NanumGothic
        else:
            # Locked NanumMyeongjo has one GPOS kern lookup, explicit pairs with XAdvance.
            pairs = face["GPOS"].table.LookupList.Lookup[0].SubTable[0]
            kern = {(left, rec.SecondGlyph): rec.Value1.XAdvance
                    for left, records in zip(pairs.Coverage.glyphs, pairs.PairSet, strict=True)
                    for rec in records.PairValueRecord}
        em = face["head"].unitsPerEm

    @cache
    def advance(a, following):
        # The locked regular faces use horizontal pair kerning for this plain-text corpus.
        if len(a) != 1 or len(following) != 1 or ord(a) not in cmap or ord(following) not in cmap:
            raise ValueError("cannot measure native glyph advance in selected trace")
        pair = cmap[ord(a)], cmap[ord(following)]
        return (metrics[pair[0]][0] + kern.get(pair, 0)) * size / em

    return advance


def _trace_spacing(line, chars, gaps, size, natural_space, remaining, left, native, where):
    where += ": selected spacing trace"
    counts = {"space": 0, "tracking": 0}
    for kind, _ in gaps:
        if kind != "native":
            counts[kind] += 1
    advances = {}
    for kind, nominal in (("tracking", size), ("space", natural_space)):
        ratio = line.get(kind + "_per_10000")
        if type(ratio) is not int:
            raise ValueError(f"{where}: invalid {kind} ratio")
        average = nominal * ratio / 10000
        total_key = kind + "_width_sp"
        if total_key in line:
            total = line[total_key]
            count = counts[kind]
            # The engine log rounds its ratio to 1/10000, but clips and applies exact sp totals.
            if type(total) is not int or (not count and total != 0):
                raise ValueError(f"{where}: invalid {total_key}")
            if count:
                exact = total * BP_PER_SP / count
                if abs(exact - average) > nominal / 20000 + 2 * BP_PER_SP:
                    raise ValueError(f"{where}: {total_key} differs from ratio")
                average = exact
        advances[kind] = average
    spaces = repeat(advances["space"])
    if "spaces_sp" in line:
        raw = line["spaces_sp"]
        if (not isinstance(raw, list) or len(raw) != counts["space"]
                or any(type(v) is not int or v <= 0 for v in raw)):
            raise ValueError(f"{where}: invalid spaces_sp")
        if "space_width_sp" in line and sum(raw) != line["space_width_sp"]:
            raise ValueError(f"{where}: spaces_sp differs from total")
        if any(abs(v * BP_PER_SP - advances["space"]) > PDF_TOLERANCE for v in raw):
            raise ValueError(f"{where}: spaces_sp differs from ratio")
        spaces = (value * BP_PER_SP for value in raw)
    if "remaining_sp" in line:
        slack = line["remaining_sp"]
        if type(slack) is not int or abs(slack * BP_PER_SP - remaining) > PDF_TOLERANCE:
            raise ValueError(f"{where}: remaining_sp differs from printed right edge")
    origin = left
    for (a, b), (kind, actual) in zip(zip(chars, chars[1:]), gaps, strict=True):
        adjustment = 0.
        if kind != "native":
            adjustment = next(spaces) if kind == "space" else advances["tracking"]
            if abs(actual - adjustment) > PDF_TOLERANCE:
                raise ValueError(f"{where}: {kind} differs from printed PDF")
        origin += native(a.text, " " if kind == "space" else b.text) + adjustment
        if abs(origin - b.x0) > PDF_TOLERANCE:
            raise ValueError(f"{where}: glyph origin differs from printed PDF")


def verify_justified_pdf(pdf, source, style, *, expected_lines=None):
    """Measure searchable physical lines, including paragraph continuations across pages."""
    from tests.pdf_metrics import read_pages

    if not pdf.startswith(b"%PDF-"):
        raise ValueError("missing printable PDF")
    word_ends, paragraph_ends, words = set(), [], []
    offset = 0
    for part in paragraphs(unicodedata.normalize("NFC", source)):
        for word in part.split():
            words.append(word)
            offset += len(word)
            word_ends.add(offset)
        paragraph_ends.append(offset)
    original = "".join(words)
    if not original:
        raise ValueError("empty manuscript source")
    face, space_em = BODY_FACES[style["body_font"]]
    size = style["body_pt"] * 72 / 72.27
    native = _native_advance(style["body_font"], size) if expected_lines is not None else None
    width, height = (n * 72 / 25.4 for n in style["page_mm"])
    top, right, bottom, left = (n * 72 / 25.4 for n in style["margins_mm"])
    tracking = [size * pct / 100 for pct in style["ko_tracking_bounds_pct"]]
    spaces = [size * space_em * pct / 100 for pct in style["space_pct"]]
    preferred_tracking = size * style["ko_tracking_pct"] / 100
    natural_space = min(max(size * space_em, spaces[0]), spaces[1])
    line_ends = []
    offset = paragraph = nonfinal = 0
    max_left = max_right = 0.
    for page_no, page in enumerate(read_pages(pdf), 1):
        if max(abs(page.width - width), abs(page.height - height)) > PDF_TOLERANCE:
            raise ValueError(f"page {page_no}: page size differs from study style")
        # The extractor can separate words into text boxes when the printed gap is wide.
        rows = defaultdict(list)
        for line in page.lines:
            for c in line.chars:
                if c.text.strip():
                    rows[round(c.baseline, 3)].append(c)
        if not rows:
            raise ValueError(f"page {page_no}: empty or non-searchable page")
        for line_no, baseline in enumerate(sorted(rows, reverse=True), 1):
            chars = sorted(rows[baseline], key=lambda c: c.x0)
            where = f"page {page_no}, line {line_no}"
            text = "".join(c.text for c in chars)
            end = offset + len(text)
            if not original.startswith(text, offset):
                raise ValueError(f"{where}: printed text differs from source at {offset}")
            if end > paragraph_ends[paragraph]:
                raise ValueError(f"{where}: line crosses paragraph boundary")
            if end not in word_ends:
                raise ValueError(f"{where}: not a source word boundary at {end}")
            left_error = abs(chars[0].x0 - left)
            max_left = max(max_left, left_error)
            if left_error > PDF_TOLERANCE:
                raise ValueError(f"{where}: left edge differs by {left_error:.3f} PDF pt")
            delta = chars[-1].x0 + chars[-1].advance - (page.width - right)
            final = end == paragraph_ends[paragraph]
            if delta > PDF_TOLERANCE or (not final and abs(delta) > PDF_TOLERANCE):
                raise ValueError(f"{where}: right edge differs by {delta:+.3f} PDF pt")
            paragraph_start = paragraph_ends[paragraph - 1] if paragraph else 0
            if final:
                paragraph += 1
            else:
                nonfinal += 1
                max_right = max(max_right, abs(delta))
            for c in chars:
                if c.font.split("+")[-1] != face or abs(c.size - size) > PDF_TOLERANCE:
                    raise ValueError(f"{where}: wrong font or size")
                if c.y0 < bottom - PDF_TOLERANCE or c.y1 > page.height - top + PDF_TOLERANCE:
                    raise ValueError(f"{where}: glyph outside vertical margins")
            gaps = _word_gaps(chars, offset, word_ends, tracking, spaces, where)
            if final:
                _natural_final(gaps, preferred_tracking, natural_space, -delta, where)
            if expected_lines is not None:
                if len(line_ends) >= len(expected_lines):
                    raise ValueError(f"{where}: missing selected trace line")
                selected = expected_lines[len(line_ends)]
                if not isinstance(selected, dict):
                    raise ValueError(f"{where}: invalid selected trace line")
                if "end_glyph" in selected and selected["end_glyph"] != end - paragraph_start:
                    raise ValueError(f"{where}: selected ends differ from printed PDF lines")
                _trace_spacing(selected, chars, gaps, size, size * space_em, -delta, left, native, where)
            offset = end
            line_ends.append(end)
    if offset != len(original):
        raise ValueError("PDF does not contain complete source")
    if expected_lines is not None and len(line_ends) != len(expected_lines):
        raise ValueError("extra selected trace lines")
    return {"line_ends": line_ends, "nonfinal_lines": nonfinal,
            "max_left_error_pt": max_left, "max_nonfinal_right_error_pt": max_right}


def spacing(dest):
    _tracking_packet(dest, "spacing", {"space_pct": [100, 100]})


def justification(dest, tracking_bounds=JUSTIFIED_TRACKING_BOUNDS, space_bounds=JUSTIFIED_SPACE_BOUNDS):
    if not all(math.isfinite(v) for v in (*tracking_bounds, *space_bounds)):
        raise ValueError("bounds must be finite percentages")
    if not (tracking_bounds[0] <= -7 and tracking_bounds[1] >= 0):
        raise ValueError("tracking bounds must include all three preferred values (-7, -3, 0)")
    if not 0 < space_bounds[0] <= space_bounds[1]:
        raise ValueError("space bounds must be positive and ordered")
    _tracking_packet(dest, "justification", {
        "paragraph_layout": "justified", "ko_tracking_bounds_pct": list(tracking_bounds),
        "space_pct": list(space_bounds),
    })


def _tracking_packet(dest, experiment, options):
    from fastapi.testclient import TestClient
    from app.server import app

    manuscripts = corpus()
    verify_split(manuscripts, [])
    manuscript, = [r for r in manuscripts if r["split"] == "spacing"]
    if experiment == "justification" and digest(manuscript["source"].encode()) != QUAY_SHA256:
        raise ValueError("justification requires the original quay manuscript")
    cells = [(font, pt, leading) for font in ("nanum-myeongjo", "nanum-gothic")
             for pt, leading in [(11, 17), (16, 24)]]
    RNG.shuffle(cells)
    with new_run(dest, experiment) as (tmp, key), TestClient(app) as client:
        for font, pt, leading in cells:
            _, group = new_group(key, manuscript)
            for tracking in RNG.sample([0, -3, -7], 3):
                style = {**PAGE, "body_font": font, "body_pt": pt, "leading_pt": leading,
                         "ko_tracking_pct": tracking, **options}
                args = {"source": manuscript["source"], "style": style}
                response, ms = measured(client.post, "/api/render", json=args)
                condition = f"{font} {pt}pt, preferred tracking {tracking}%"
                if experiment == "spacing":
                    response.raise_for_status()
                elif not response.is_success:
                    raise ValueError(f"{condition}: render HTTP {response.status_code}: {response.text[:500]}")
                if response.headers["content-type"] != "application/pdf":
                    raise ValueError("render returned no PDF")
                evidence = {"style": style, "ms": ms}
                if experiment == "justification":
                    try:
                        evidence["verification"] = verify_justified_pdf(response.content, manuscript["source"], style)
                    except ValueError as exc:
                        raise ValueError(f"{condition}: {exc}") from exc
                add_pdf(tmp, group, response.content, evidence)
