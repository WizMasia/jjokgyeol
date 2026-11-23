# Document Fonts Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Check each task against the approved design.

**Goal:** Choose device fonts in either editor and embed each face where its rights allow.

**Architecture:** Keep inspection local in the editor and validate again in the isolated render worker. Pass only selected faces in a bounded binary render request. Both editors share the document codec and font picker.

**Tech Stack:** Static JavaScript, Python, LuaLaTeX, Tauri/Rust, zip.js, pytest and Playwright.

**Spec:** [Approved design](../specs/2026-11-23-document-fonts-design.md)

## Global constraints

Preserve v1 reading, unmodified legacy API calls, optional proof PDF and standalone integer styles. Compare SHA-256 of the actual bytes. Support static TTF and OTF. Collect permission before transferring a face to the web renderer. Keep source JSON, font, archive and worker limits distinct.

## Review focus

Inspect conflicting license evidence, archive manifest tampering, permissions changing between save and reopen, async results arriving after document replacement, and native binary transport of multi-megabyte Korean fonts.

## Tasks

1. **Inspection.** Write competing permission fixtures first. Implement `FontRights.inspect(blob, known)` in the static editor and the equivalent `inspect_face(data)` in Python. Compare results for installable, editable, preview-only, restricted, missing, OFL, no-subsetting, bitmap-only and unsupported faces. Keep linked notices offline. Verify actual PDF/redistribution decisions in both implementations.
2. **Engine.** Exercise standalone and nested style references, missing weights, and actual PDF streams before changing `app/style.py`, `app/typeset.py` and the Lua loader. Use `font-<regular>-<bold>` with lowercase SHA-256 or `none` for each face. Implement task-local `use_document_fonts(assets)`; retain original refs and fallback per weight in engine policy. Confirm rule and fixed model layout.
3. **Archive.** Extend `app/static/jjok-archive.js` with v2 face records `{hash,name,version,weight,file?,notice?}` at its JS boundary. Verify before packing or returning, deduplicate files and notices, bound entries before extraction, read v1. Test round-trip, proof on/off, malformed references, unknown rights and tampering.
4. **Transport and native selection.** A font render frame contains a four-byte big-endian JSON header length, UTF-8 JSON `{request,faces:[{hash,size}]}`, then concatenated raw files in order. Test malformed, duplicate, oversized and truncated frames. Decode once in `app/font_frame.py`. Web uses `application/x-jjok-font-render`; native uses raw Tauri IPC and the existing isolated model-proof worker for rule or model. Native lists installed files without exposing arbitrary filesystem reads; return bytes only for listed opaque identifiers. Test offline desktop bridge and platform enumeration.
5. **Editor.** Add device/file selection and evidence view, dynamic font choices, session permission to send to the displayed web destination, fallback notices, archive snapshots, and exact reference recovery. Keep web/native requests with no new fonts compatible. Test without network, after permission denial, and after switching documents while busy.
6. **Integration and delivery.** Publish locked face/notice metadata via bootstrap and supply exact bytes in both editions. Run editor, archive, native, PDF and existing regression suites; inspect production PDF streams and exchange v2 archives. Build local web and Mac trials, run a fresh whole-change review and commit verified changes locally.

For each task, first run its new failing test, implement and rerun, then check the diff. Run pytest for Python and browser regressions, cargo test for the native bridge, and syntax checks for changed static scripts. Compare packaged runtime output with known-good local fonts.
