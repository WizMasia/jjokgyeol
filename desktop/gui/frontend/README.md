# Desktop frontend

Install Node.js/npm and Python 3.11 or newer. Activate a Python environment with `markdown-it-py==4.3.0` installed, then run:

```sh
cd desktop/gui/frontend
npm ci
npm run build
npm test
```

The build creates `dist/index.html`, editor assets, the sample manuscript, and the locked PDF.js, worker, character maps and standard fonts. Tauri must expose `window.__TAURI__.core.invoke`; its runtime supplies style conversion, rule rendering and save dialogs. No server is used by the app.

For the optional browser integration test, install Python Playwright and its Chromium browser, then run `python -m unittest test_desktop`. Set `CHROME_PATH` to use an existing Chromium executable instead. The test serves the static files locally only for browser automation and supplies a mock Tauri bridge.

To check a `.jjok` round trip with a running Docker web editor, set `JJOKGYEOL_REFERENCE_URL` to its local URL and run `python -m unittest test_document_interop` after building the frontend. The test saves a web proof, opens it in the built desktop UI, verifies the embedded PDF bytes, then excludes the PDF and reopens the document in the web editor.
