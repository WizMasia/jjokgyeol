"use strict";

const desktop = {
  async invoke(command, args, options) {
    try {
      return options === undefined
        ? await window.__TAURI__.core.invoke(command, args)
        : await window.__TAURI__.core.invoke(command, args, options);
    } catch (value) {
      let detail = value;
      if (typeof value === "string") {
        try { detail = JSON.parse(value); } catch { detail = {message: value}; }
      }
      const error = new Error(detail?.message || "데스크톱 작업에 실패했습니다.");
      error.field = detail?.field;
      error.status = detail?.status || 422;
      throw error;
    }
  },
  async style(action, body) {
    return this.invoke("style_convert", {action, body: typeof body === "string" ? body : await body.text()});
  },
  async proof(source, style) {
    const stored = await this.style("export", JSON.stringify(style));
    const result = await this.invoke("render_proof", {source, style: stored});
    return new Blob([new Uint8Array(result)], {type: "application/pdf"});
  },
  async save(blob, name) {
    // JSON 배열 직렬화 증폭을 피하고 원시 바이트로 IPC 전송 / Send raw binary payload with encoded filename header.
    const bytes = new Uint8Array(await blob.arrayBuffer());
    return this.invoke("save_bytes", bytes, {
      headers: {"x-file-name": encodeURIComponent(String(name ?? ""))},
    });
  },
  setDirty(dirty) {
    return this.invoke("set_dirty", {dirty});
  },
  setStyleDirty(dirty) {
    return this.invoke("set_style_dirty", {dirty});
  },
  confirmDocumentReplacement() {
    return this.invoke("confirm_document_replacement");
  },
  confirmStyleReplacement() {
    return this.invoke("confirm_style_replacement");
  },
  async pages(blob) {
    const pdfjs = await import("./pdf.min.mjs");
    pdfjs.GlobalWorkerOptions.workerSrc = new URL("static/pdf.worker.min.mjs", document.baseURI).href;
    const pdf = await pdfjs.getDocument({data: new Uint8Array(await blob.arrayBuffer()),
      isEvalSupported: false,
      cMapUrl: new URL("static/cmaps/", document.baseURI).href, cMapPacked: true,
      standardFontDataUrl: new URL("static/standard_fonts/", document.baseURI).href}).promise;
    const pages = document.createElement("div");
    pages.id = "desktop-pages";
    try {
      for (let n = 1; n <= pdf.numPages; n++) {
        const page = await pdf.getPage(n);
        const viewport = page.getViewport({scale: 1.5});
        const canvas = document.createElement("canvas");
        canvas.setAttribute("aria-label", n + "쪽");
        canvas.width = Math.ceil(viewport.width);
        canvas.height = Math.ceil(viewport.height);
        await page.render({canvasContext: canvas.getContext("2d"), viewport}).promise;
        pages.append(canvas);
      }
      return pages;
    } finally { await pdf.destroy(); }
  },
};
