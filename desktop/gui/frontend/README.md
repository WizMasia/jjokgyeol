# Desktop frontend

Install Node.js/npm and Python 3.11 or newer. Activate a Python environment with `markdown-it-py==4.3.0` installed, then run:

```sh
cd desktop/gui/frontend
npm ci
npm run build
npm test
```

The build creates `dist/index.html`, editor assets, the sample manuscript, and the locked PDF.js, worker, character maps and standard fonts. Tauri must expose `window.__TAURI__.core.invoke`; its runtime supplies style conversion, rule rendering and save dialogs. The app serves its own assets and performs rule rendering locally. The frontend uses native IPC to retrieve model IDs, check external connections and generate model proofs. **새 문서 기본값으로 저장** saves the draft style and public connection settings in the WebView profile. The API key stays in session memory. Natural-language style suggestions are available in the Docker web editor.

For browser integration tests, install Python Playwright and its Chromium browser, then run `python -m unittest test_desktop test_settings test_decision_settings_native`. These exercise the editor, defaults across WebView-profile restarts and the native model-selection controls. Set `CHROME_PATH` to use an existing Chromium executable instead. The test serves the static files locally only for browser automation and supplies a mock Tauri bridge.

To check a `.jjok` round trip with a running Docker web editor, set `JJOKGYEOL_REFERENCE_URL` to its local URL and run `python -m unittest test_document_interop` after building the frontend. The test saves a web proof, opens it in the built desktop UI, verifies the embedded PDF bytes, then excludes the PDF and reopens the document in the web editor.
