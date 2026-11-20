"""Export real /api/render responses using the separately mounted T14 protocol."""
from functools import partial
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import jobs
from app.ai.decision import PIN
from app.server import app
from evaluation.model import MODEL_STYLE, inspect_pdf, load_arm
from evaluation.packet import blind_pdf, corpus, digest, measured, read_json, save_json, verify_split


def capture(client, folder, manuscript, mode, style):
    with TemporaryDirectory() as scratch:
        # Only defer cleanup in this private operator process, until its trace is copied.
        keep = partial(TemporaryDirectory, dir=scratch, delete=False)
        with patch.object(jobs, "TemporaryDirectory", keep):
            response, ms = measured(client.post, "/api/render", json={
                "source": manuscript["source"], "style": style, "decision": mode})
        response.raise_for_status()
        applied = response.headers["X-Jjokgyeol-Decision"]
        job, = Path(scratch).iterdir()
        trace = job / "paragraph-candidates.jsonl"
        if not trace.exists():
            trace = job / ("measure" if applied == "rule" else "replay") / trace.name
        if (trace.parent / "book.pdf").read_bytes() != response.content:
            raise ValueError("final trace and returned PDF come from different passes")
        log = trace.read_bytes()

    folder.mkdir(parents=True)
    (folder / trace.name).write_bytes(log)
    (folder / "render.pdf").write_bytes(response.content)
    save_json(folder / "measurement.json", {
        "applied": applied, "elapsed_ms": ms, "style": style,
        "source_sha256": digest(manuscript["source"].encode("utf-8")),
        "pdf_sha256": digest(response.content), "log_sha256": digest(log),
        "model_sha256": PIN["files"]["model.safetensors"] if applied in ("model", "mixed") else None})
    print(manuscript["group"], mode, applied, round(ms), "ms", flush=True)


def audit_pair(group, manuscript):
    pages, catalogs, outcomes = [], [], {}
    for arm in ("rules", "model"):
        folder = group / arm
        receipt = read_json(folder / "measurement.json")
        pdf = (folder / "render.pdf").read_bytes()
        log = (folder / "paragraph-candidates.jsonl").read_bytes()
        if (receipt["source_sha256"] != digest(manuscript["source"].encode())
                or receipt["pdf_sha256"] != digest(pdf) or receipt["log_sha256"] != digest(log)):
            raise ValueError("receipt hash mismatch")
        rows = [json.loads(line) for line in log.splitlines()]
        catalogs.append([(r["rule_id"], r["candidates"]) for r in rows])
        pages.append(digest(blind_pdf(pdf)))
        selected, offset = [], 0
        for row in rows:
            choice, = [c for c in row["candidates"] if c["id"] == row["selected_id"]]
            selected.extend(offset + line["end_glyph"] for line in choice["lines"])
            offset = selected[-1]
        printed, violations = inspect_pdf(pdf, manuscript)
        mismatch = max(len(set(selected) - set(printed)), len(set(printed) - set(selected)))
        # Keep genuine agreements and the protocol's rejection in the producer evidence.
        rejection = None
        try:
            load_arm(folder, manuscript, arm, PIN["files"]["model.safetensors"])
        except ValueError as exc:
            rejection = str(exc)
        outcomes[arm] = {"applied": receipt["applied"], "elapsed_ms": receipt["elapsed_ms"],
                         "changed_blocks": [r["block"] for r in rows if r["selected_id"] != r["rule_id"]],
                         "selected_ends": selected, "pdf_ends": printed, "end_mismatches": mismatch,
                         "pdf_constraint_violations": violations, "t14_rejection": rejection}
    save_json(group / "outcome.json", {"arms": outcomes, "same_candidate_sets": catalogs[0] == catalogs[1],
                                      "distinct_printed_pages": pages[0] != pages[1]})


def export(dest):
    if dest.exists():
        raise FileExistsError(dest)
    fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/decision-dew.md"
    development = [{"group": "decision-dew", "split": "dev", "source": fixture.read_text("utf-8")},
                   {"group": "decision-dew", "split": "dev",
                    "source": "풀잎 사이 Dew 은빛 이슬\n\n" * 3}]
    manuscripts = corpus()
    verify_split(manuscripts, development)
    with TemporaryDirectory(dir=dest.parent) as tmp, TestClient(app) as client:
        stage = Path(tmp)
        for ms in manuscripts:
            if ms["split"] == "model":
                capture(client, stage / ms["group"] / "rules", ms, "rule", MODEL_STYLE)
                capture(client, stage / ms["group"] / "model", ms, "model", MODEL_STYLE)
                audit_pair(stage / ms["group"], ms)
        save_json(stage / "manifest.json", {
            "runtime": f'{PIN["variant"]} / {PIN["runtime"]["name"]}@{PIN["runtime"]["version"]}',
            "model_sha256": PIN["files"]["model.safetensors"], "development": development})
        stage.rename(dest)


if __name__ == "__main__":
    export(Path(sys.argv[1]))
