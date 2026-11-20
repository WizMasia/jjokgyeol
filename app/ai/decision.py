"""Select from measured layouts on one operator-owned LM Studio instance."""
from dataclasses import dataclass
import hashlib
from http.client import HTTPConnection, HTTPException
import json
import os
import signal
import subprocess
from pathlib import Path
from time import monotonic
from unicodedata import category

from app.configuration import CONFIG_DIR, read_config, unique_fields
from app.document import InputError
from app.style import Style
from app.typeset import typeset

PIN = read_config(CONFIG_DIR / "decision.json")
MODEL_DIR = Path("/opt/decision-model")
MODEL_ERRORS = (OSError, HTTPException, ValueError, TypeError, KeyError, IndexError, RecursionError)
NUMBERS = ("end_glyph", "available_sp", "remaining_sp", "start_sp", "tracking_per_10000",
           "space_per_10000", "tracking_delta", "space_deviation", "tracking_change", "space_change")


def file_identity():
    identity = []
    for name in PIN["files"]:
        stat = (MODEL_DIR / name).stat()
        identity.append((stat.st_ino, stat.st_size, stat.st_ctime_ns))
    return identity


@dataclass
class LocalModel:
    host: str
    port: int
    identity: list

    @classmethod
    def verified(cls):
        host = os.getenv("JJOKGYEOL_DECISION_HOST")
        if host not in ("127.0.0.1", "host.docker.internal"):
            return None
        try:
            port = int(os.getenv("JJOKGYEOL_DECISION_PORT", "1234"))
            if not 1 <= port <= 65535:
                return None
            model = cls(host, port, file_identity())
            for name, digest in PIN["files"].items():
                with (MODEL_DIR / name).open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                        return None
            if model.loaded() and file_identity() == model.identity:
                return model
        except MODEL_ERRORS:
            pass
        return None

    def request(self, path, data=None):
        # A fixed origin and direct connection exclude redirects and proxy settings.
        conn = HTTPConnection(self.host, self.port, timeout=PIN["decision_seconds"])
        try:
            conn.request("GET" if data is None else "POST", path,
                         None if data is None else json.dumps(data).encode(),
                         {"Content-Type": "application/json"})
            response = conn.getresponse()
            if response.status != 200:
                raise ValueError("model unavailable")
            raw = response.read(PIN["max_response_bytes"] + 1)
            if len(raw) > PIN["max_response_bytes"]:
                raise ValueError("oversize response")
            return json.loads(raw, object_pairs_hook=unique_fields)
        finally:
            conn.close()

    def loaded(self):
        rows = self.request("/api/v1/models")["models"]
        return any(row["key"] == PIN["model"] and row.get("selected_variant") == PIN["variant"]
                   and any(instance["id"] == PIN["model"] for instance in row["loaded_instances"])
                   for row in rows)

    def predict(self, options):
        if file_identity() != self.identity or not self.loaded():
            return None
        schema = {"type": "object", "properties": {"id": {"type": "string", "enum": list(options)}},
                  "required": ["id"], "additionalProperties": False}
        response = self.request("/api/v0/chat/completions", {
            "model": PIN["model"], "temperature": 0, "max_tokens": 96, "stream": False,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "paragraph", "strict": True, "schema": schema}},
            "messages": [{"role": "system", "content":
                          "Choose a paragraph layout with balanced line lengths, including the last "
                          "line. Avoid isolated glyphs, split words and abrupt spacing changes. "
                          "Widths are in sp; spacing ratios use 10000 as one unit. Return its ID."},
                         {"role": "user", "content": json.dumps(options, separators=(",", ":"))}]})
        if (response["model"] != PIN["model"] or
                any(response[group][k] != v for group in ("model_info", "runtime")
                    for k, v in PIN[group].items()) or file_identity() != self.identity):
            return None
        choice, = response["choices"]
        if choice["finish_reason"] == "stop":
            answer = json.loads(choice["message"]["content"], object_pairs_hook=unique_fields)
            if isinstance(answer, dict) and answer.keys() == {"id"}:
                return answer["id"]
        return None


def features(lines, style, final_glyph):
    track = style.ko_tracking_bounds_per_10000 or (style.ko_tracking_per_10000,) * 2
    if not isinstance(lines, list) or not 1 <= len(lines) <= PIN["max_lines_per_candidate"]:
        return None
    result = []
    previous = 0
    for line in lines:
        if not isinstance(line, dict) or any(type(line.get(k)) is not int for k in NUMBERS):
            return None
        if not (previous < line["end_glyph"] <= final_glyph and line["available_sp"] > 0
                and 0 <= line["remaining_sp"] <= line["available_sp"]
                and track[0] <= line["tracking_per_10000"] <= track[1]
                and style.space_per_10000[0] <= line["space_per_10000"] <= style.space_per_10000[1]
                and type(line.get("split_word")) is bool):
            return None
        row = {key: line[key] for key in NUMBERS}
        for edge in ("first_char", "last_char"):
            if edge not in line:
                row[edge + "_class"] = "box"
                continue
            code = line[edge]
            if type(code) is not int or not 0 <= code <= 0x10ffff:
                return None
            row[edge + "_class"] = category(chr(code))
        row["split_word"] = line["split_word"]
        result.append(row)
        previous = line["end_glyph"]
    return result if previous == final_glyph else None


@dataclass
class ParagraphDecision:
    style: Style
    rule_id: str
    model: LocalModel | None
    used_model: bool = False

    def choose(self, candidates: list[dict]) -> str:
        self.used_model = False
        if self.model is None or len(candidates) < 2:
            return self.rule_id
        try:
            rule, = [item for item in candidates if item["id"] == self.rule_id]
            total = rule["lines"][-1]["end_glyph"]
            options = {}
            for candidate in candidates:
                key = candidate["id"]
                if not isinstance(key, str) or key in options:
                    return self.rule_id
                lines = features(candidate["lines"], self.style, total)
                if lines:
                    options[key] = lines
            if self.rule_id in options and len(options) > 1:
                selected = self.model.predict(options)
                if isinstance(selected, str) and selected in options:
                    self.used_model = True
                    return selected
        except MODEL_ERRORS:
            pass
        return self.rule_id


class DecisionExpired(Exception):
    pass


def expire(signum, frame):
    raise DecisionExpired


def measured_rows(folder):
    with (folder / "paragraph-candidates.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def render_choice(doc, style, workdir, deadline):
    """Called in a single-threaded POSIX render worker. Keep its rule proof for fallback."""
    measure = workdir / "measure"
    measure.mkdir()
    baseline = typeset(doc, style, measure, collect_candidates=True)
    fallback = (baseline, "rule")
    remaining = min(PIN["decision_seconds"], deadline - PIN["delivery_reserve_seconds"] - monotonic())
    if remaining <= 0:
        return fallback
    old_handler = signal.signal(signal.SIGALRM, expire)
    try:
        # Socket timeouts alone restart after each read and cannot bound TeX replay.
        signal.setitimer(signal.ITIMER_REAL, remaining)
        model = None
        choices = {}
        mixed = False
        for row in measured_rows(measure):
            if len(row["candidates"]) < 2:
                continue
            if model is None:
                model = LocalModel.verified()
                if model is None:
                    return fallback
            selector = ParagraphDecision(style, row["rule_id"], model)
            selected = selector.choose(row["candidates"])
            if selector.used_model:
                choices[row["block"]] = selected
            else:
                mixed = True
        if not choices:
            return fallback
        replay = workdir / "replay"
        replay.mkdir()
        pdf = typeset(doc, style, replay, paragraph_choices=choices, collect_candidates=True)
        applied = {row["block"]: row["selected_id"] for row in measured_rows(replay)
                   if row["block"] in choices}
        if applied == choices:
            return pdf, "mixed" if mixed else "model"
    except (*MODEL_ERRORS, DecisionExpired, subprocess.CalledProcessError, InputError):
        pass
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
    return fallback
