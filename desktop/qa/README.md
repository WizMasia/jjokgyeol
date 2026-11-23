# Windows x64 development verification

Use this procedure to compare a Windows runtime with the checked-in duplex PDF. The [native Windows workflow](../../.github/workflows/native-proof.yml) builds and checks the runtime. The [release workflow](../../.github/workflows/native-release.yml) also installs and exercises the Windows setup executable.

1. Install 64-bit Python 3.12 and Rust stable with the MSVC x64 toolchain. For the comparator and its tests, run `py -3.12 -m pip install --require-hashes --only-binary=:all: -r desktop/qa/requirements-compare.lock`. The repository's `requirements.lock` is for Linux arm64 and is not a Windows dependency lock.
2. Build/provision the development runtime using the worker's documented build/package steps. Place it in a local directory containing `runtime.json`, `worker/jjokgyeol-worker.exe`, the configured LuaLaTeX engine and fonts. Never use a system TeX installation as an implicit fallback.
3. From the repository root in PowerShell, run the checked-in duplex fixture (replace the runtime path):

   ```powershell
   cargo run --manifest-path desktop/Cargo.toml -- --runtime C:\path\to\runtime --source proofs/numbering/manuscript.md --style proofs/numbering/odd-even.json --output "$env:TEMP\jjokgyeol-odd-even.pdf"
   py -3.12 desktop/qa/compare_pdfs.py "$env:TEMP\jjokgyeol-odd-even.pdf" proofs/numbering/odd-even.pdf
   ```

The comparator reads both PDFs with `tests/pdf_metrics.py`, checks page count and dimensions, line and glyph text and count, subset-normalized font names and character geometry (x/y bounds, baseline, advance and size) with a 0.05 pt tolerance. It reports the first difference and exits nonzero. Malformed input also fails. The checked-in duplex line-number baseline is 2 pages with SHA-256 `dfe31ad480c731816706431f7be2242f6d7965c86f4e239754e827d92ef30439`.

Run comparator checks with `py -3.12 -m unittest discover -s desktop/qa -p 'test_*.py'`. For the CI resource export and PowerShell verification commands, see the [Windows runtime guide](runtime/README.md).
