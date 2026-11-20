"""Local operator CLI. Run from the repository root in the locked test image."""
import argparse
import csv
import json
from pathlib import Path

from evaluation.model import model_pairs, revision
from evaluation.packet import JUSTIFIED_SPACE_BOUNDS, JUSTIFIED_TRACKING_BOUNDS, justification, spacing
from evaluation.results import capture, score, timing, timing_decision


def main():
    parser = argparse.ArgumentParser(description="쪽결 블라인드 판독 / Blind reading")
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("spacing", "justification", "pairs", "capture", "timing", "timing-decision", "score"):
        p = commands.add_parser(name)
        p.add_argument("run", type=Path)
        if name == "justification":
            p.add_argument("--tracking-bounds", nargs=2, type=float, default=JUSTIFIED_TRACKING_BOUNDS, metavar=("MIN", "MAX"),
                           help="absolute final Hangul tracking percentages (default %(default)s)")
            p.add_argument("--space-bounds", nargs=2, type=float, default=JUSTIFIED_SPACE_BOUNDS, metavar=("MIN", "MAX"),
                           help="word space percentages (default %(default)s)")
        elif name == "pairs":
            p.add_argument("--justified", action="store_true")
            p.add_argument("references", type=Path)
            p.add_argument("bundle", type=Path)
        elif name == "capture":
            p.add_argument("sheet", type=Path)
            p.add_argument("--reader", required=True)
        elif name == "timing-decision":
            p.add_argument("decision", choices=["accepted", "rejected"])
            p.add_argument("--by", required=True)
    p = commands.add_parser("reference")
    p.add_argument("references", type=Path)
    p.add_argument("group")
    p.add_argument("pdf", type=Path)
    p.add_argument("--justified", action="store_true")
    p.add_argument("--editor", required=True)
    p.add_argument("--paragraph", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--final", action="store_true")
    a = parser.parse_args()
    profile = {"justified": True} if getattr(a, "justified", False) else {}
    try:
        if a.action == "reference":
            result = revision(a.references, a.group, a.pdf.read_bytes(), a.editor, a.paragraph, a.reason, a.final, **profile)
        elif a.action in ("spacing", "justification"):
            if a.action == "justification":
                justification(a.run, a.tracking_bounds, a.space_bounds)
            else:
                spacing(a.run)
            result = {"reader": str(a.run / "reader")}
        elif a.action == "pairs":
            model_pairs(a.run, a.references, a.bundle, **profile)
            result = {"reader": str(a.run / "reader")}
        elif a.action == "capture":
            capture(a.run, a.sheet, a.reader)
            result = {"captured": True}
        elif a.action == "timing-decision":
            timing_decision(a.run, a.decision, a.by)
            result = {"recorded": a.decision}
        elif a.action == "timing":
            result = timing(a.run)
        else:
            result = score(a.run)
    except (ValueError, OSError, csv.Error) as exc:
        parser.exit(2, f"{exc}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
