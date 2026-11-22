"""Export real /api/render responses using the separately mounted T14 protocol."""
import argparse
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import jobs
from app.ai.decision import PIN
from app.server import app
from evaluation.model import load_arm, model_style
from evaluation.packet import blind_pdf, corpus, digest, measured, save_json, verify_split


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


def audit_pair(group, manuscript, *, justified=False):
    pages, catalogs, outcomes = [], [], {}
    for arm in ("rules", "model"):
        pdf, _, receipt, catalog, evidence = load_arm(
            group / arm, manuscript, arm, PIN["files"]["model.safetensors"], justified=justified, audit=True)
        catalogs.append(catalog)
        pages.append(digest(blind_pdf(pdf)))
        fields = ("changed_blocks", "selected_ends", "pdf_ends", "end_mismatches",
                  "pdf_constraint_violations", "t14_rejection")
        outcome = {key: evidence[key] for key in fields}
        if justified:
            outcome["verification"] = evidence.get("verification")
            outcome["constraint_violations"] = evidence["constraint_violations"]
        outcomes[arm] = {**outcome, "applied": receipt["applied"], "elapsed_ms": receipt["elapsed_ms"]}
    save_json(group / "outcome.json", {"arms": outcomes, "same_candidate_sets": catalogs[0] == catalogs[1],
                                      "distinct_printed_pages": pages[0] != pages[1]})


def export(dest, *, justified=False):
    if dest.exists():
        raise FileExistsError(dest)
    fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/decision-dew.md"
    development = [{"group": "decision-dew", "split": "dev", "source": fixture.read_text("utf-8")},
                   {"group": "decision-dew", "split": "dev",
                    "source": "풀잎 사이 Dew 은빛 이슬\n\n" * 3}]
    manuscripts = corpus()
    verify_split(manuscripts, development)
    with TemporaryDirectory(dir=dest.parent) as tmp, TestClient(app, base_url="http://127.0.0.1") as client:
        stage = Path(tmp)
        style = model_style(justified)
        for ms in manuscripts:
            if ms["split"] == "model":
                capture(client, stage / ms["group"] / "rules", ms, "rule", style)
                capture(client, stage / ms["group"] / "model", ms, "model", style)
                audit_pair(stage / ms["group"], ms, justified=justified)
        save_json(stage / "manifest.json", {
            "runtime": f'{PIN["variant"]} / {PIN["runtime"]["name"]}@{PIN["runtime"]["version"]}',
            "model_sha256": PIN["files"]["model.safetensors"], "development": development})
        stage.rename(dest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dest", type=Path)
    parser.add_argument("--justified", action="store_true")
    args = parser.parse_args()
    export(args.dest, justified=args.justified)
