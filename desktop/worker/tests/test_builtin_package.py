"""Freeze a first-party fixture with a small data file using the production spec."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


def test_frozen_builtin_can_read_its_weights(tmp_path):
    freezer = shutil.which("pyinstaller")
    if not freezer:
        pytest.skip("requires the worker build environment")
    root = Path(__file__).resolve().parents[3]
    shutil.copytree(root / "desktop/worker", tmp_path / "desktop/worker")
    shutil.copytree(root / "app", tmp_path / "app", ignore=shutil.ignore_patterns("static", "__pycache__"))
    shutil.copy(root / "runtime.lock", tmp_path)
    model_dir = tmp_path / "app/ai"
    (model_dir / "weights").mkdir(exist_ok=True)
    (model_dir / "weights/fixture.bin").write_bytes(b"b")
    (model_dir / "trained_decision.py").write_text(
        'from pathlib import Path\ndef predict(options, *, timeout):\n'
        '    return Path(__file__).with_name("weights").joinpath("fixture.bin").read_text()\n',
        encoding="utf-8",
    )
    subprocess.run([freezer, "--noconfirm", "desktop/worker/worker.spec"], cwd=tmp_path,
                   capture_output=True, check=True, timeout=180)
    worker = tmp_path / "dist/jjokgyeol-worker" / ("jjokgyeol-worker.exe" if os.name == "nt" else "jjokgyeol-worker")
    checked = subprocess.run([worker, "--check-decision"], cwd=tmp_path, input=b'{"provider":"builtin"}',
                             capture_output=True, timeout=30)
    assert checked.returncode == 0, checked.stderr
    assert json.loads(checked.stdout) == {"ok": True}
