# Rule render worker

Build on the target OS and architecture in a clean Python 3.12 environment. Install the repository's hash-locked `requirements.lock`, then `python -m pip install --require-hashes --only-binary=:all: -r desktop/worker/build-requirements.lock`; run `python -m PyInstaller --clean --noconfirm desktop/worker/worker.spec` from the repository root. Copy the contents of `dist/jjokgyeol-worker` into `runtime/worker`. The build includes the publication configuration, Lua templates, and `runtime.lock`; it does not include TeX or fonts.

Supply `runtime/runtime.json` with relative `engine` and `font_dir` paths and provide a compatible LuaLaTeX installation and the locked font files there. The caller creates a fresh job directory containing UTF-8 `request.json`, invokes `worker/jjokgyeol-worker --runtime <absolute runtime directory>` with that directory as its current directory, and reads `response.pdf` on exit 0. Failures exit nonzero and write JSON to stderr. The invoking process owns the deadline, process-tree cleanup, and concurrent render limit.

This build is a development prototype. Dependencies are pinned and hash checked; byte-for-byte reproducibility and platform-specific engine bundles require separate validation.
