"""Model spacing is selected from measured, source-free options on the accepted line proof."""
import json
from time import monotonic

import pytest

from app.ai import decision, providers
from app.ai.spacing import verify_report
from app.ai.builtin import BuiltinDecision
from app.document import parse_md
from app.style import SP_PER_PT, Style, apply_style
from pdf_metrics import read_pages
from test_builtin_decision import bundled_module
from test_decision import candidates, installed, model_server, write_rows
from test_decision_providers import api, raw, reply


def boundary(index, *, source="rule"):
    return dict(id=str(index), before_line=index * 2 - 4, after_line=index * 2 - 2,
                gap_sp=4 * SP_PER_PT, source=source, auto=source == "rule",
                page=1, remaining_sp=30 * SP_PER_PT, lines=2,
                left_kind="paragraph", right_kind="heading", right_level=2, valid=True)


class Engine:
    def __init__(self, rows, *, line_choices=True, damage=None):
        self.rows = rows
        self.line_choices = line_choices
        self.damage = damage
        self.calls = []

    def __call__(self, doc, style, folder, *, collect_candidates, paragraph_choices=None,
                 spacing_choices=None):
        assert collect_candidates
        self.calls.append((folder.name, paragraph_choices, spacing_choices))
        line = dict(block=1, rule_id="rule", selected_id=(paragraph_choices or {}).get(1, "rule"),
                    candidates=candidates()[:2])
        rows = [dict(r) for r in self.rows]
        for row in rows:
            if paragraph_choices:
                row["remaining_sp"] -= SP_PER_PT
            if row["id"] in (spacing_choices or {}):
                row.update(gap_sp=spacing_choices[row["id"]], source="ai")
        pdf = b"%PDF-" + folder.name.encode() + b"\n%%EOF"
        if spacing_choices and self.damage:
            pdf = self.damage(rows, line, pdf)
        if self.line_choices:
            write_rows(folder, [line])
        else:
            (folder / "paragraph-candidates.jsonl").touch()
        (folder / "spacing.json").write_text(json.dumps(rows))
        (folder / "book.pdf").write_bytes(pdf)
        return pdf


class Selector:
    def __init__(self):
        self.options = []

    def predict(self, options, *, timeout):
        return "other"

    def predict_spacing(self, options, *, timeout):
        assert 0 < timeout <= decision.PIN["decision_seconds"]
        self.options.append(options)
        return max(options, key=lambda key: options[key]["gap_sp"])


def render(root, model, engine):
    return decision.render_provider_choice(None, Style(), root, monotonic() + 40, model,
                                           typesetter=engine)


def report(folder):
    return json.loads((folder / "spacing.json").read_bytes())


def test_selects_only_automatic_gaps_on_accepted_line_proof(tmp_path):
    rows = [boundary(2, source="manual"), boundary(3, source="style"),
            dict(boundary(4), private="secret manuscript", font="secret font")]
    rows[0]["gap_sp"] = 0
    engine, model = Engine(rows), Selector()
    pdf, status, reason = render(tmp_path, model, engine)
    assert (status, reason) == ("model", None)
    options, = model.options
    assert {v["gap_sp"] for v in options.values()} == {4 * SP_PER_PT, 11 * SP_PER_PT // 2, 11 * SP_PER_PT}
    assert all(v["remaining_sp"] == 29 * SP_PER_PT and v["right_level"] == 2 for v in options.values())
    assert "secret" not in json.dumps(options)
    assert engine.calls[-1][1:] == ({1: "other"}, {"4": 11 * SP_PER_PT})
    assert len(engine.calls) == 3
    assert pdf == b"%PDF-" + engine.calls[-1][0].encode() + b"\n%%EOF"
    assert report(tmp_path) == report(tmp_path / engine.calls[-1][0])
    assert [r["source"] for r in report(tmp_path)] == ["manual", "style", "ai"]
    assert report(tmp_path / "measure") == rows


def test_recovered_page_boundary_uses_document_gap_from_original_render(tmp_path):
    requested = tmp_path / "measure"
    requested.mkdir()
    (requested / "input.json").write_text(json.dumps({
        "style": {"paragraph_gap_sp": 9 * SP_PER_PT}, "spacing": {"2": {"auto": False}},
    }))
    old = dict(boundary(2, source="default"), page=2, left_page=1, gap_sp=0)
    new = dict(old, page=1, left_page=1, gap_sp=9 * SP_PER_PT)
    verify_report([old], [new], {}, requested)


def test_spacing_alone_and_document_call_limit(tmp_path, monkeypatch):
    monkeypatch.setitem(decision.PIN, "max_spacing_calls_per_document", 2)
    model, engine = Selector(), Engine([boundary(i) for i in range(2, 24)], line_choices=False)
    _, status, _ = render(tmp_path, model, engine)
    assert len(model.options) == 2 and len(engine.calls) == 2
    assert status == "mixed"
    assert [r["source"] for r in report(tmp_path)] == ["ai"] * 2 + ["rule"] * 20


@pytest.mark.parametrize("fault", ["unknown", "timeout", "invalid-proof", "wrong-gap", "wrong-source",
                                   "clobbered-fixed", "lost-line-choice", "missing-row", "incomplete-pdf"])
def test_failed_spacing_returns_accepted_line_pdf_and_its_own_report(tmp_path, fault):
    def damage(rows, line, pdf):
        if fault == "invalid-proof":
            rows[0]["valid"] = False
        elif fault == "wrong-gap":
            rows[-1]["gap_sp"] = 0
        elif fault == "wrong-source":
            rows[-1]["source"] = "rule"
        elif fault == "clobbered-fixed":
            rows[0]["gap_sp"] += 1
        elif fault == "lost-line-choice":
            line["selected_id"] = "rule"
        elif fault == "missing-row":
            rows.pop()
        return b"%PDF-partial" if fault == "incomplete-pdf" else pdf

    model = Selector()
    if fault == "unknown":
        model.predict_spacing = lambda options, *, timeout: "invented"
    if fault == "timeout":
        def expire(options, *, timeout):
            raise TimeoutError
        model.predict_spacing = expire
    engine = Engine([boundary(2, source="style"), boundary(3)], damage=damage)
    pdf, status, reason = render(tmp_path, model, engine)
    assert (pdf, status, reason) == (b"%PDF-replay\n%%EOF", "mixed",
                                     "timeout" if fault == "timeout" else "invalid_response")
    assert report(tmp_path) == report(tmp_path / "replay")


def test_legacy_line_only_selector_preserves_lines_and_rule_spacing(tmp_path):
    class Lines:
        def predict(self, options, *, timeout):
            return "other"

    engine = Engine([boundary(2)])
    assert render(tmp_path, Lines(), engine) == (b"%PDF-replay\n%%EOF", "mixed", "unavailable")
    assert len(engine.calls) == 2 and report(tmp_path) == report(tmp_path / "replay")


def test_late_spacing_result_cannot_replace_validated_lines(tmp_path, monkeypatch):
    now = 10.
    monkeypatch.setattr(decision, "monotonic", lambda: now)
    monkeypatch.delattr(decision.signal, "setitimer", raising=False)

    def late(rows, line, pdf):
        nonlocal now
        now += decision.PIN["decision_seconds"]
        return pdf

    assert render(tmp_path, Selector(), Engine([boundary(2)], damage=late)) == (
        b"%PDF-replay\n%%EOF", "mixed", "timeout")
    assert report(tmp_path) == report(tmp_path / "replay")


@pytest.mark.parametrize("change", [dict(valid=False), dict(auto=False), dict(left_kind="SECRET-MANUSCRIPT"),
                                    dict(source="manual"), dict(remaining_sp=-1)])
def test_ineligible_or_invalid_boundary_never_reaches_selector(tmp_path, change):
    model, engine = Selector(), Engine([dict(boundary(2), **change)], line_choices=False)
    pdf, status, _ = render(tmp_path, model, engine)
    assert (pdf, status) == (b"%PDF-measure\n%%EOF", "rule")
    assert model.options == [] and len(engine.calls) == 1


@pytest.mark.parametrize("provider", providers.DEFAULTS)
def test_existing_connections_send_spacing_prompt_and_enforce_timeout(api, provider):
    options = {"a": {"gap_sp": 0}, "b": {"gap_sp": 123}}
    connection = providers.parse_connection(raw(provider, api.base))
    api.respond = lambda body: (200, {}, reply(provider, '{"id":"b"}'))
    assert connection.predict_spacing(options, timeout=1) == "b"
    assert "vertical" in json.dumps(api.calls[0][2]).lower()
    with pytest.raises(providers.ProviderError, match="timeout"):
        connection.predict_spacing(options, timeout=0)
    assert len(api.calls) == 1


def test_builtin_uses_only_optional_spacing_entrypoint(bundled_module):
    model = BuiltinDecision()
    options = {"a": {"gap_sp": 0}}
    bundled_module.predict = lambda *a, **kw: pytest.fail("called line selector for spacing")
    with pytest.raises(providers.ProviderError, match="builtin_unavailable"):
        model.predict_spacing(options, timeout=1)

    def spacing(choices, *, timeout):
        assert 0 < timeout <= 1
        choices["a"].clear()
        return "a"

    bundled_module.predict_spacing = spacing
    assert model.predict_spacing(options, timeout=1) == "a"
    assert options["a"] == {"gap_sp": 0}


def test_verified_operator_selects_spacing_without_line_candidates(installed, monkeypatch, tmp_path):
    inventory, response = installed
    calls = []

    def answer(path, data):
        if data is None:
            return inventory
        calls.append(data)
        options = json.loads(data["messages"][-1]["content"])
        response["choices"][0]["message"]["content"] = json.dumps({"id": next(iter(options))})
        return response

    with model_server(answer) as port:
        monkeypatch.setenv("JJOKGYEOL_DECISION_PORT", str(port))
        engine = Engine([boundary(2)], line_choices=False)
        assert render(tmp_path, None, engine)[1:] == ("model", None)
        model = decision.LocalModel.verified()
        with pytest.raises(providers.ProviderError, match="timeout"):
            model.predict_spacing({}, timeout=0)
    call, = calls
    assert "vertical" in call["messages"][0]["content"].lower()
    assert report(tmp_path)[0]["source"] == "ai"


def test_supervisor_marker_exists_before_spacing_inference_only_after_validated_lines(tmp_path):
    class Probe(Selector):
        def predict_spacing(self, options, *, timeout):
            assert (tmp_path / "line-replay-ready").read_bytes() == b""
            assert (tmp_path / "replay/book.pdf").read_bytes() == b"%PDF-replay\n%%EOF"
            assert report(tmp_path / "replay")[0]["source"] == "rule"
            return super().predict_spacing(options, timeout=timeout)

    assert render(tmp_path, Probe(), Engine([boundary(2)]))[1:] == ("model", None)


@pytest.mark.parametrize("line_choices,source", [(False, "rule"), (True, "manual")])
def test_supervisor_marker_requires_line_replay_and_automatic_gap(tmp_path, line_choices, source):
    render(tmp_path, Selector(), Engine([boundary(2, source=source)], line_choices=line_choices))
    assert not (tmp_path / "line-replay-ready").exists()


def test_actual_pdf_and_its_spacing_report_match_the_accepted_choice(tmp_path):
    from test_decision import STYLE

    doc = parse_md("봄\n\n풀잎 사이 Dew 은빛 이슬")
    doc.blocks[0].space_after_sp = "auto"
    class Model(Selector):
        def predict(self, options, *, timeout):
            return min(options, key=lambda key: options[key][0]["end_glyph"])

    def printed(pdf):
        page, = read_pages(pdf)
        return [(line.text, line.chars[0].baseline) for line in page.lines]

    pdf, status, reason = decision.render_provider_choice(
        doc, apply_style(Style(), STYLE), tmp_path, monotonic() + 30, Model())
    assert (status, reason) == ("model", None)
    chosen, = report(tmp_path)
    assert (chosen["source"], chosen["gap_sp"]) == ("ai", 11 * SP_PER_PT)
    line_pdf = printed((tmp_path / "replay/book.pdf").read_bytes())
    gap_pdf = printed(pdf)
    assert len(gap_pdf) == len(line_pdf) == 3
    for i, ((text, before), (actual, after)) in enumerate(zip(line_pdf, gap_pdf, strict=True)):
        assert text == actual
        assert before - after == pytest.approx((7 if i else 0) * 72 / 72.27, abs=.02)

@pytest.mark.parametrize("fault", ["missing", "invalid", "fixed-change"])
def test_line_replay_must_preserve_measured_spacing_before_checkpoint(tmp_path, fault):
    engine = Engine([boundary(2, source="manual"), boundary(3)])

    def bad_line_proof(doc, style, folder, **kwargs):
        pdf = engine(doc, style, folder, **kwargs)
        if folder.name == "replay":
            rows = report(folder)
            if fault == "missing":
                (folder / "spacing.json").unlink()
            else:
                if fault == "invalid":
                    rows[0]["valid"] = False
                else:
                    rows[0]["gap_sp"] += 1
                (folder / "spacing.json").write_text(json.dumps(rows))
        return pdf

    pdf, status, reason = render(tmp_path, Selector(), bad_line_proof)
    assert not (tmp_path / "line-replay-ready").exists()
    assert (status, reason) == ("mixed", "invalid_response")
    # Spacing alone still succeeds using the rule line layout.
    assert pdf == (tmp_path / "spacing/book.pdf").read_bytes()
    assert engine.calls[-1][1] == {}
    assert report(tmp_path)[0] == report(tmp_path / "measure")[0]


def test_gap_pushed_to_page_edge_reports_zero_and_preserves_line_layout(tmp_path):
    def to_new_page(rows, line, pdf):
        rows[0].update(page=2, left_page=1, gap_sp=0)
        return pdf

    _, status, reason = render(tmp_path, Selector(), Engine([boundary(2)], damage=to_new_page))
    assert (status, reason) == ("model", None)
    assert report(tmp_path)[0]["gap_sp"] == 0


def test_configured_candidate_ratios_deduplicate_and_clamp(tmp_path, monkeypatch):
    from decimal import Decimal
    from app import publication

    monkeypatch.setattr(publication, "PARAGRAPH_SPACING", {
        "range_pt": [0, 7], "default_pt": 3, "candidate_body_ratios": [Decimal(".5"), 1, 2]})
    model = Selector()
    render(tmp_path, model, Engine([dict(boundary(2), gap_sp=3 * SP_PER_PT)], line_choices=False))
    options, = model.options
    assert [v["gap_sp"] for v in options.values()] == [3 * SP_PER_PT, 11 * SP_PER_PT // 2, 7 * SP_PER_PT]
