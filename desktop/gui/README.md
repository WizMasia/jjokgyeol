# Native GUI bundle

The installed app uses a bundled WebView frontend, a Rust supervisor and a local typesetting worker. Use the settings window to select a decision model and save publication defaults for new documents. Rule rendering works offline. Model discovery contacts the selected server when requested. See the [user guide](../../README.md) and [release build instructions](../release/README.md).

Build the offline frontend first using the steps in [frontend/README.md](frontend/README.md). Prepare an absolute runtime directory containing runtime.json, the worker/jjokgyeol-worker executable (or .exe on Windows), the engine file, and the font directory named by runtime.json.

From this directory, run python bundle.py /absolute/path/to/runtime on macOS or Windows. The script validates the runtime and frontend assets, then creates a macOS app bundle or Windows installer.
