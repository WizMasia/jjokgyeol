# Native render worker

Build on the target OS and architecture from the repository root with a clean Python 3.12 environment. The worker parses Markdown with `markdown-it-py` and `mdurl` and uses the standard library to contact decision APIs. The frozen build includes fallback TLS roots. Install the worker lockfiles for the target OS and architecture as shown below.

```sh
python3.12 -m venv .venv-worker
# macOS/Linux: source .venv-worker/bin/activate
# Windows PowerShell: .venv-worker\Scripts\Activate.ps1
python -m pip install --require-hashes --only-binary=:all: -r desktop/worker/requirements.lock -r desktop/worker/build-requirements.lock
python -m PyInstaller --clean --noconfirm desktop/worker/worker.spec
```

Copy the contents of `dist/jjokgyeol-worker` into `runtime/worker`. The build includes the publication configuration, Lua templates, and `runtime.lock`; it does not include TeX or fonts. The release workflow builds the worker on macOS arm64, Windows x64 and Ubuntu x86_64, then verifies the installed runtime on each platform. See the [packaging and verification procedure](../release/README.md).

Supply `runtime/runtime.json` with relative `engine` and `font_dir` paths and provide a compatible LuaLaTeX installation and the locked font files there. The caller creates a fresh job directory containing UTF-8 `request.json`, invokes `worker/jjokgyeol-worker --runtime <absolute runtime directory>` (on Windows, `jjokgyeol-worker.exe`) with that directory as its current directory, and reads `response.pdf` on exit 0. Failures exit nonzero and write JSON to stderr. The invoking process owns the deadline, process-tree cleanup, and concurrent render limit.

The worker bounds request reads and PDF output. On POSIX, it also applies the configured per-file size limit to itself and child processes. Windows does not yet enforce a per-file quota on engine output; the PDF is checked before it is read.

Worker tests are run with `PYTHONPATH=. python -m pytest desktop/worker/tests/test_worker.py` (on Windows PowerShell, set `$env:PYTHONPATH = '.'` first). The tests exercise the CLI separately and mock only the typesetter's engine subprocess for successful and failed renders; no shell-based engine is required.

For native style files, invoke the same executable with either `--style-open` or `--style-export` and no `--runtime`. Send one UTF-8 JSON object on stdin (a UTF-8 BOM is accepted); stdout contains the converted JSON object. Open converts stored integer units to editor values, and export converts editor values to stored units. Input is limited to the configured request size; invalid input exits nonzero with a JSON error on stderr. These modes do not render or require a runtime directory.

For session-scoped model rendering, the GUI supervisor invokes `--model-proof <runtime>` and sends one JSON object with `source`, stored `style`, and `connection` over stdin. It returns the measured rule PDF and, when model choices pass replay verification, the selected PDF. `--check-decision` takes the connection alone on stdin and tests a synthetic choice. The regular `--runtime` CLI continues to use the local rule renderer.

In the preview.4 worktree, to list model IDs invoke the native `list_decision_models` command with `{"connection":{"provider":"lmstudio","base_url":"http://127.0.0.1:1234/v1"}}`. The supervisor sends the connection, including an optional `api_key`, to `--list-decision-models` over private stdin. The worker queries the catalog and writes `{"models":["model-id"]}` to stdout. An empty list is a successful response. Catalog requests share the connection check’s render slots, size limit, deadline, and public error codes.
