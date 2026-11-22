"use strict";

function modelProofFiles(result) {
  const bytes = new Uint8Array(result);
  try {
    const start = 4 + new DataView(bytes.buffer).getUint32(0, false);
    if (start > bytes.length) throw new RangeError();
    const header = new TextDecoder("utf-8", {fatal: true}).decode(bytes.subarray(4, start));
    const {decision, reason, rule_bytes} = JSON.parse(header);
    const split = start + rule_bytes;
    if (!Number.isSafeInteger(rule_bytes) || rule_bytes <= 0 || split > bytes.length ||
        !["rule", "model", "mixed"].includes(decision) || (decision === "rule") !== (split === bytes.length)) {
      throw new Error();
    }
    const pdf = data => new Blob([data], {type: "application/pdf"});
    return {decision, reason, rule: pdf(bytes.subarray(start, split)),
      model: decision === "rule" ? null : pdf(bytes.subarray(split))};
  } catch {
    throw new Error("올바른 조판 PDF를 받지 못했습니다. 다시 갱신하세요.");
  }
}

const desktop = {
  async invoke(command, args, options) {
    try {
      return options === undefined
        ? await window.__TAURI__.core.invoke(command, args)
        : await window.__TAURI__.core.invoke(command, args, options);
    } catch (value) {
      let detail = value;
      if (typeof value === "string") {
        try { detail = JSON.parse(value); } catch { detail = {code: value, message: value}; }
      }
      const error = new Error(detail?.message || "데스크톱 작업에 실패했습니다.");
      error.code = detail?.code;
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
  async modelProof(source, style, connection) {
    const stored = await this.style("export", JSON.stringify(style));
    const result = await this.invoke("render_model_proof", {source, style: stored, connection}).catch(error => {
      if (error.code === "invalid_input") throw error;
      throw new Error(decisionMessage(error.code) || decisionMessages.unavailable);
    });
    return modelProofFiles(result);
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
