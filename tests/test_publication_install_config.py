import json
import subprocess
import sys

from app.configuration import CONFIG_DIR


def test_publication_import_accepts_pre_leading_install_config(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    publication = json.loads((CONFIG_DIR / "publication.json").read_text())
    publication.pop("paragraph_leading", None)
    (config / "publication.json").write_text(json.dumps(publication))
    (config / "numbering.json").write_bytes((CONFIG_DIR / "numbering.json").read_bytes())
    script = (
        "import sys; from pathlib import Path; import app.configuration as c; "
        "c.CONFIG_DIR = Path(sys.argv[1]); import app.publication; print('loaded')"
    )
    result = subprocess.run([sys.executable, "-c", script, str(config)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "loaded"
