# Windows x64 development verification

This is a development check, not a certification. The Windows x64 run has **not been executed in this environment**; results remain unverified until run on Windows.

1. Install 64-bit Python 3.11 and Rust stable with the MSVC x64 toolchain. Install the repository's Python dependencies (`pip install -r requirements.txt`, including `pdfminer.six`).
2. Build/provision the development runtime using the worker's documented build/package steps. Place it in a local directory containing `runtime.json`, `worker/jjokgyeol-worker.exe`, the configured LuaLaTeX engine and fonts. Never use a system TeX installation as an implicit fallback.
3. From the repository root, run `cargo run --manifest-path desktop/Cargo.toml -- --runtime <DIR> --source <fixture.md> --style <fixture.json> --output <native.pdf>`.
4. Obtain the web proof PDF from the established Docker-backed web flow without replacing its baseline. Compare: `python desktop/qa/compare_pdfs.py <native.pdf> <web.pdf>`.

The comparator reads both PDFs with `tests/pdf_metrics.py`, checks page count, line text, subset-normalized font names and character geometry (x/y bounds, baseline, advance and size) with a 0.05 pt tolerance. It reports the first page/line/metric difference and exits nonzero. Malformed input also fails. The planned duplex line-number baseline is 2 pages with SHA-256 `dfe31ad480c731816706431f7be2242f6d7965c86f4e239754e827d92ef30439`.

Run comparator checks with `python -m unittest discover -s desktop/qa -p 'test_*.py'`. CI can use the same commands on `windows-latest`; no hosted workflow was added before integration.
