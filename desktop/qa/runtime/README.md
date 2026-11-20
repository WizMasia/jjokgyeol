# Windows native proof

From the repository root on a Docker host (Linux or Mac), export the exact Debian engine-stage resources:

```sh
desktop/qa/runtime/export-resources.sh /tmp/jjokgyeol-resources.tar
```

Copy that tar to a Windows x64 machine with Python 3.12, Node.js 22/npm, PowerShell 7, Rust stable/MSVC, and tar on PATH. In PowerShell from the repository root:

```powershell
python -m pip install markdown-it-py==4.3.0 mdurl==0.1.2 pdfminer.six==20260107 pypdf==7.1.1
python -m pip install --require-hashes --only-binary=:all: -r desktop/worker/build-requirements.lock
./desktop/qa/runtime/verify.ps1 C:\path\resources.tar "$env:TEMP\jjokgyeol-native-proof"
```

Use a fresh work directory each run. The script downloads TeX Live 2022's original Windows launcher and DLL archives and validates their SHA-512 against its published package database, assembles the supplied resource tree, and rebuilds the LuaLaTeX format locally. It indexes the format tree, checks its location with the bundled `kpsewhich`, and starts the `lualatex` launcher. It builds the worker, runs Rust tests, renders the numbering fixture and compares it to the checked-in PDF. Results, the PDF when produced, and command logs are in `results`; the process returns nonzero on failure. Rust test failure is recorded while the proof is still attempted. The package database is fetched over HTTPS but is not independently signature-verified.

To test the portable setup helpers on any machine: `python -m unittest discover -s desktop/qa/runtime -p 'test_*.py'`. The workflow runs only on pushes to `feature/native-desktop`; it uploads the Windows results and does not publish a release.

After runtime provisioning and worker build, the script attempts the tracked numbering render and PDF comparison, then runs the GUI checks even if either proof-stage command fails. A render or comparison failure remains in the overall result. The script sets JJOKGYEOL_TEST_DIAGNOSTICS=1 only for the stock CLI render and restores its prior value before diagnostic reruns and GUI checks. It installs the locked GUI frontend dependencies with npm ci, builds and tests the frontend using the shared style configuration, and runs locked GUI Cargo tests and a debug build. It sets JJOKGYEOL_NATIVE_RUNTIME to the provisioned runtime, then starts the executable without bundling or distributing an installer. The results/gui-smoke-status.txt file records whether the editor and settings-window interaction passed, failed, or could not be determined because the input desktop was unavailable. On an interactive desktop, the smoke test locates the editor’s 설정 button by its accessibility name, invokes it through UI Automation, observes a distinct 출판 설정 window in the same process, sends that window a system close request, and verifies that the editor remains open. Observed process, window handles, and interaction stages are saved in results/gui-smoke-observations.txt. The Windows smoke check expects the native title 쪽결, configured in desktop/gui/tauri.conf.json; the HTML document title is 쪽결 · 원고 편집기, and Tauri uses its explicit window title for the native window. An inconclusive smoke check emits a workflow warning and is recorded separately in result.txt; a GUI build, launch, accessibility interaction, settings-window, or close failure on an accessible desktop fails the run.

The workflow runs for proof or GUI inputs, including application sources and configuration, desktop files, the Docker engine definition, and the numbering and GUI sample fixtures. A documentation-only change outside those paths does not trigger it.

If the CLI render fails, the script reruns the checked-in fixture with the explicit runtime in a fresh development job and saves its traceback, input JSON, layout diagnostic, and LuaTeX log and tail when available. The GUI checks still run, while the original CLI failure remains the run result and cannot be turned into a success by the rerun.
