"""Choose and verify paragraph layouts against the measured rule proof."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from http.client import HTTPConnection, HTTPException
import json
import os
from shutil import copyfile
import signal
import subprocess
from pathlib import Path
from threading import current_thread, main_thread
from time import monotonic
from unicodedata import category

from app.ai import spacing
from app.ai.model import DecisionModel, LIMITS as PIN, ProviderError, SPACING_PROMPT
from app.configuration import unique_fields
from app.document import InputError
from app.security import complete_pdf
from app.style import Style
from app.typeset import typeset

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
        except ProviderError:
            raise
        except MODEL_ERRORS:
            pass
        return None

    def request(self, path, data=None, *, timeout=None):
        # A fixed origin and direct connection exclude redirects and proxy settings.
        conn = HTTPConnection(self.host, self.port, timeout=PIN["decision_seconds"] if timeout is None else timeout)
        try:
            conn.request("GET" if data is None else "POST", path,
                         None if data is None else json.dumps(data).encode(),
                         {"Content-Type": "application/json"})
            response = conn.getresponse()
            if response.status == 429:
                raise ProviderError("rate_limit")
            if response.status != 200:
                raise ValueError("model unavailable")
            raw = response.read(PIN["max_response_bytes"] + 1)
            if len(raw) > PIN["max_response_bytes"]:
                raise ValueError("oversize response")
            return json.loads(raw, object_pairs_hook=unique_fields)
        finally:
            conn.close()

    def loaded(self, *, timeout=None):
        rows = self.request("/api/v1/models", timeout=timeout)["models"]
        return any(row["key"] == PIN["model"] and row.get("selected_variant") == PIN["variant"]
                   and any(instance["id"] == PIN["model"] for instance in row["loaded_instances"])
                   for row in rows)

    def predict(self, options):
        return self._predict(options, "paragraph",
                             "Choose a paragraph layout with balanced line lengths, including the last "
                             "line. Avoid isolated glyphs, split words and abrupt spacing changes. "
                             "Widths are in sp; spacing ratios use 10000 as one unit. Return its ID.",
                             PIN["decision_seconds"])

    def predict_spacing(self, options, *, timeout):
        return self._predict(options, "paragraph_spacing", SPACING_PROMPT, timeout)

    def _predict(self, options, name, prompt, timeout):
        end = monotonic() + min(timeout, PIN["decision_seconds"])
        if timeout <= 0:
            raise ProviderError("timeout")
        if file_identity() != self.identity or not self.loaded(timeout=seconds_left(end)):
            return None
        schema = {"type": "object", "properties": {"id": {"type": "string", "enum": list(options)}},
                  "required": ["id"], "additionalProperties": False}
        response = self.request("/api/v0/chat/completions", {
            "model": PIN["model"], "temperature": 0, "max_tokens": 96, "stream": False,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": name, "strict": True, "schema": schema}},
            "messages": [{"role": "system", "content": prompt},
                         {"role": "user", "content": json.dumps(options, separators=(",", ":"))}]},
            timeout=seconds_left(end))
        seconds_left(end)
        if (response["model"] != PIN["model"] or
                any(response[group][k] != v for group in ("model_info", "runtime")
                    for k, v in PIN[group].items()) or file_identity() != self.identity):
            return None
        choice, = response["choices"]
        if choice["finish_reason"] == "stop":
            answer = json.loads(choice["message"]["content"], object_pairs_hook=unique_fields)
            if isinstance(answer, dict) and answer.keys() == {"id"}:
                return answer["id"] if isinstance(answer["id"], str) and answer["id"] in options else None
        return None


def features(lines, style, final_glyph):
    track = style.ko_tracking_bounds_per_10000 or (style.ko_tracking_per_10000,) * 2
    if not isinstance(lines, list) or not lines:
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
    model: LocalModel | DecisionModel | None
    used_model: bool = False
    reason: str | None = None

    def choose(self, candidates: list[dict], *, timeout=None) -> str:
        self.used_model = False
        self.reason = None
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
                if not isinstance(candidate["lines"], list) or len(candidate["lines"]) > PIN["max_lines_per_candidate"]:
                    continue
                lines = features(candidate["lines"], self.style, total)
                if lines:
                    options[key] = lines
            if self.rule_id in options and len(options) > 1:
                if timeout is None:
                    selected = self.model.predict(options)
                else:
                    selected = self.model.predict(options, timeout=timeout)
                if isinstance(selected, str) and selected in options:
                    self.used_model = True
                    return selected
        except ProviderError as error:
            self.reason = error.code
        except MODEL_ERRORS:
            pass
        return self.rule_id


class DecisionExpired(TimeoutError):
    pass


def expire(signum, frame):
    raise DecisionExpired


def measured_rows(folder):
    previous = 0
    with (folder / "paragraph-candidates.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line, object_pairs_hook=unique_fields)
            block = row["block"]
            if type(block) is not int or block <= previous:
                raise ValueError("invalid paragraph block order")
            previous = block
            yield row


def candidate_catalog(row):
    """One block's rule and measurements, keyed by opaque ID as in T14."""
    candidates = row["candidates"]
    if not isinstance(candidates, list):
        raise ValueError("invalid candidates")
    by_id = {}
    for candidate in candidates:
        key = candidate["id"]
        if not isinstance(key, str) or not key.strip() or key in by_id:
            raise ValueError("invalid or duplicate candidate ID")
        by_id[key] = candidate["lines"]
    rule = row["rule_id"]
    if rule not in by_id or row["selected_id"] not in by_id:
        raise ValueError("unmeasured rule or selection")
    return rule, by_id


def replay_status(doc, style, measure, replay, choices, mixed, *, typesetter=typeset):
    honored = 0
    catalog_changed = False
    for before, after in zip(measured_rows(measure), measured_rows(replay), strict=True):
        block = before["block"]
        old_rule, old = candidate_catalog(before)
        rule, fresh = candidate_catalog(after)
        if block != after["block"] or before["selected_id"] != old_rule:
            return "rule"
        # A surviving ID must still describe exactly what the selector saw.
        if any(old[key] != fresh[key] for key in old.keys() & fresh.keys()):
            return "rule"
        total = old[old_rule][-1]["end_glyph"]
        if any(features(lines, style, total) is None for lines in fresh.values()):
            return "rule"
        if old == fresh and rule != old_rule:
            return "rule"
        catalog_changed |= old != fresh
        selected = after["selected_id"]
        if selected == choices.get(block):
            honored += 1
        elif selected != rule:
            return "rule"
    if not honored:
        return "rule"
    if catalog_changed:
        # IDs can expire. Independently reproduce the current rule and catalog
        # before accepting a partial replay with a different measurement set.
        check = replay.parent / "rule-check"
        check.mkdir()
        if not complete_pdf(typesetter(doc, style, check, collect_candidates=True)):
            return "rule"
        for proof, after in zip(measured_rows(check), measured_rows(replay), strict=True):
            if (proof["block"] != after["block"] or proof["selected_id"] != proof["rule_id"]
                    or candidate_catalog(proof) != candidate_catalog(after)):
                return "rule"
    return "mixed" if mixed or honored < len(choices) else "model"


def seconds_left(end):
    remaining = end - monotonic()
    if remaining <= 0:
        raise DecisionExpired
    return remaining


@contextmanager
def decision_timer(end):
    remaining = seconds_left(end)
    # The native supervisor interrupts a blocked engine on platforms without SIGALRM.
    interruptible = hasattr(signal, "setitimer") and current_thread() is main_thread()
    if interruptible:
        old_handler = signal.signal(signal.SIGALRM, expire)
    try:
        if interruptible:
            signal.setitimer(signal.ITIMER_REAL, remaining)
        yield
    finally:
        if interruptible:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
        seconds_left(end)


def render_choice(doc, style, workdir, deadline):
    """Legacy operator-selected model and two-value result."""
    pdf, status, _ = render_provider_choice(doc, style, workdir, deadline, None, typesetter=typeset)
    return pdf, status


def measure_rule(doc, style, measure, *, typesetter=typeset):
    measure.mkdir()
    baseline = typesetter(doc, style, measure, collect_candidates=True)
    if not complete_pdf(baseline):
        raise ValueError("incomplete rule PDF")
    return baseline


def render_provider_choice(doc, style, workdir, deadline, connection, *, typesetter=typeset, baseline_ready=None):
    measure = workdir / "measure"
    baseline = measure_rule(doc, style, measure, typesetter=typesetter)
    end = min(monotonic() + PIN["decision_seconds"], deadline - PIN["delivery_reserve_seconds"])
    if baseline_ready is not None:
        baseline_ready(end)
    model = connection

    def get_model():
        nonlocal model
        if model is None:
            model = LocalModel.verified()
            if model is None:
                raise ProviderError("unavailable")
        return model

    pdf, status, reason = _line_proof(doc, style, measure, baseline, end, get_model,
                                      connection is None, typesetter)
    folder = measure if status == "rule" else workdir / "replay"
    if (folder / "spacing.json").is_file():
        if reason not in TERMINAL_ERRORS:
            try:
                # Leave time to deliver the accepted proof if the spacing pass expires.
                spacing_end = end - PIN["delivery_reserve_seconds"]
                rows = spacing.read_report(folder)
                automatic = [r for r in rows if r["auto"] is True and r["source"] == "rule" and spacing.measured(r)]
                if automatic:
                    with decision_timer(spacing_end):
                        refined, partial = _spacing_proof(doc, style, folder, pdf, rows, automatic,
                                                          get_model(), spacing_end, typesetter)
                    pdf, folder = refined, workdir / "spacing"
                    status = "mixed" if partial or reason else "model"
            except (TimeoutError, subprocess.TimeoutExpired):
                reason = "timeout"
            except ProviderError as error:
                reason = error.code
            except (*MODEL_ERRORS, subprocess.CalledProcessError):
                reason = "invalid_response"
            if reason and status == "model":
                status = "mixed"
        copyfile(folder / "spacing.json", workdir / "spacing.json")
    return pdf, status, reason


TERMINAL_ERRORS = {"authentication", "model", "address", "rate_limit", "builtin_unavailable",
                   "unavailable", "timeout"}


def _spacing_proof(doc, style, folder, accepted_pdf, rows, automatic, model, end, typesetter):
    predict = getattr(model, "predict_spacing", None)
    if predict is None:
        raise ProviderError("unavailable")
    if folder.name == "replay":
        saved = folder / "book.pdf"
        if saved.is_file() and saved.read_bytes() == accepted_pdf:
            (folder.parent / "line-replay-ready").touch(exist_ok=False)
    limit = PIN["max_spacing_calls_per_document"]
    choices = {}
    for row in automatic[:limit]:
        options = spacing.options(row, style.body_sp)
        offered = {key: fields["gap_sp"] for key, fields in options.items()}
        selected = predict(options, timeout=seconds_left(end))
        seconds_left(end)
        if not isinstance(selected, str) or selected not in offered:
            raise ProviderError("invalid_response")
        choices[row["id"]] = offered[selected]

    prior = list(measured_rows(folder))
    line_choices = {row["block"]: row["selected_id"] for row in prior
                    if row["selected_id"] != row["rule_id"]}
    replay = folder.parent / "spacing"
    replay.mkdir()
    pdf = typesetter(doc, style, replay, collect_candidates=True,
                     paragraph_choices=line_choices, spacing_choices=choices)
    seconds_left(end)
    if not complete_pdf(pdf):
        raise ValueError("incomplete spacing PDF")
    spacing.verify_report(rows, spacing.read_report(replay), choices, folder)
    for before, after in zip(prior, measured_rows(replay), strict=True):
        if (before["block"] != after["block"] or before["selected_id"] != after["selected_id"]
                or candidate_catalog(before) != candidate_catalog(after)):
            raise ValueError("spacing replay changed paragraph layout")
    return pdf, len(automatic) > limit


def _line_proof(doc, style, measure, baseline, end, get_model, legacy, typesetter):
    try:
        with decision_timer(end):
            choices = {}
            reason = None
            for row in measured_rows(measure):
                rule, catalog = candidate_catalog(row)
                total = catalog[rule][-1]["end_glyph"]
                if row["selected_id"] != rule or any(features(lines, style, total) is None
                                                      for lines in catalog.values()):
                    return baseline, "rule", "invalid_response"
                if len(catalog) < 2:
                    continue
                model = get_model()
                remaining = seconds_left(end)
                selector = ParagraphDecision(style, rule, model)
                selected = selector.choose(row["candidates"], timeout=None if legacy else remaining)
                seconds_left(end)
                if selector.used_model:
                    choices[row["block"]] = selected
                elif selector.reason in ("authentication", "model", "address", "rate_limit", "builtin_unavailable"):
                    reason = selector.reason
                    break
                else:
                    reason = reason or selector.reason or "invalid_response"
            if not choices:
                return baseline, "rule", reason
            seconds_left(end)
            replay = measure.parent / "replay"
            replay.mkdir()
            pdf = typesetter(doc, style, replay, paragraph_choices=choices, collect_candidates=True)
            seconds_left(end)
            if complete_pdf(pdf):
                status = replay_status(doc, style, measure, replay, choices, reason is not None, typesetter=typesetter)
                if status != "rule" and (measure / "spacing.json").is_file():
                    spacing.verify_report(spacing.read_report(measure), spacing.read_report(replay), {}, measure)
                if status == "model":
                    return pdf, status, None
                if status == "mixed":
                    return pdf, status, reason or "invalid_response"
    except (DecisionExpired, TimeoutError, subprocess.TimeoutExpired):
        return baseline, "rule", "timeout"
    except ProviderError as error:
        return baseline, "rule", error.code
    except (*MODEL_ERRORS, subprocess.CalledProcessError, InputError):
        pass
    return baseline, "rule", "invalid_response"
