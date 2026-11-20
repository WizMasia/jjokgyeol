# Native GUI bundle

Build the offline frontend first using the steps in [frontend/README.md](frontend/README.md). Prepare an absolute runtime directory containing runtime.json, the worker/jjokgyeol-worker executable (or .exe on Windows), the engine file, and the font directory named by runtime.json.

From this directory, run python bundle.py /absolute/path/to/runtime on macOS or Windows. The script validates the runtime and frontend assets, then creates a macOS app bundle or Windows installer.
