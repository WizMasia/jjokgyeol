"""Create local, single-reader packets with a separate operator key."""
import csv
import hashlib
import json
import os
import random
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

HERE = Path(__file__).resolve().parent
HEADER = (HERE / "reading-sheet.csv").read_text().strip().split(",")
RNG = random.SystemRandom()
STOCK = {"quay": "spacing", "drawer": "model", "platform": "model"}
PAGE = {"page_mm": [148, 210], "margins_mm": [20, 18, 20, 18]}


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


def spacing(dest):
    from fastapi.testclient import TestClient
    from app.server import app

    manuscripts = corpus()
    verify_split(manuscripts, [])
    manuscript, = [r for r in manuscripts if r["split"] == "spacing"]
    cells = [(font, pt, leading) for font in ("nanum-myeongjo", "nanum-gothic")
             for pt, leading in [(11, 17), (16, 24)]]
    RNG.shuffle(cells)
    with new_run(dest, "spacing") as (tmp, key), TestClient(app) as client:
        for font, pt, leading in cells:
            _, group = new_group(key, manuscript)
            for tracking in RNG.sample([0, -3, -7], 3):
                style = {**PAGE, "body_font": font, "body_pt": pt, "leading_pt": leading,
                         "ko_tracking_pct": tracking, "space_pct": [100, 100]}
                args = {"source": manuscript["source"], "style": style}
                response, ms = measured(client.post, "/api/render", json=args)
                response.raise_for_status()
                if response.headers["content-type"] != "application/pdf":
                    raise ValueError("render returned no PDF")
                add_pdf(tmp, group, response.content, {"style": style, "ms": ms})
