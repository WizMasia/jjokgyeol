# Windows native proof

From the repository root on a Docker host (Linux or Mac), export the exact Debian engine-stage resources:

```sh
desktop/qa/runtime/export-resources.sh /tmp/jjokgyeol-resources.tar
```

Copy that tar to a Windows x64 machine with Python 3.12, Rust stable/MSVC, and tar on PATH. In PowerShell from the repository root:

```powershell
python -m pip install markdown-it-py==4.3.0 mdurl==0.1.2 pdfminer.six==20260107 pypdf==7.1.1
python -m pip install --require-hashes --only-binary=:all: -r desktop/worker/build-requirements.lock
./desktop/qa/runtime/verify.ps1 C:\path\resources.tar "$env:TEMP\jjokgyeol-native-proof"
```

Use a fresh work directory each run. The script downloads TeX Live 2022's original Windows launcher and DLL archives and validates their SHA-512 against its published package database, assembles the supplied resource tree, and rebuilds the LuaLaTeX format locally. It indexes the format tree, checks its location with the bundled `kpsewhich`, and starts the `lualatex` launcher. It builds the worker, runs Rust tests, renders the numbering fixture and compares it to the checked-in PDF. Results, the PDF when produced, and command logs are in `results`; the process returns nonzero on failure. Rust test failure is recorded while the proof is still attempted. The package database is fetched over HTTPS but is not independently signature-verified.

To test the portable setup helpers on any machine: `python -m unittest discover -s desktop/qa/runtime -p 'test_*.py'`. The workflow runs only on pushes to `feature/native-desktop`; it uploads the Windows results and does not publish a release.
