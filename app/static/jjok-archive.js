"use strict";

globalThis.JjokArchive = (() => {
  // @zip.js/zip.js 2.25.0, dist/zip-native.min.js. Its JS fallback also runs offline.
  const zipOptions = {useWebWorkers: false};
  const requiredNames = ["manifest.json", "style.json", "manuscript.md"];
  const pdfName = "proof.pdf";
  const assetBase = document.currentScript.src;
  const sizeError = "문서가 허용 크기를 초과합니다.";

  class DocumentError extends Error {}

  function requireValue(condition, message) {
    if (!condition) throw new DocumentError(message);
  }

  function checkLimits(limits) {
    requireValue([limits?.request, limits?.file].every(n => Number.isSafeInteger(n) && n > 0),
      "문서 크기 설정을 확인하세요.");
  }

  function entrySize(name, size, total, limits) {
    requireValue(Number.isSafeInteger(size) && size >= 0 && size <= limits.file - total &&
      (name === pdfName || size <= limits.request), sizeError);
    return total + size;
  }

  function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function storedNumber(key, value) {
    requireValue(typeof value !== "number" || Number.isSafeInteger(value), "양식의 숫자는 정수여야 합니다.");
    return value;
  }

  function proofInfo(proof) {
    requireValue(isObject(proof) && ["current", "stale"].includes(proof.state) &&
      ["rule", "model", "mixed"].includes(proof.mode), "교정 PDF의 상태와 조판 방식을 확인하세요.");
    return {state: proof.state, mode: proof.mode};
  }

  async function checkPdf(blob) {
    const message = "교정 PDF가 손상되었거나 올바른 PDF 파일이 아닙니다.";
    const header = await blob.slice(0, 10).text();
    requireValue(/^%PDF-(?:1\.[0-7]|2\.0)[\r\n]/.test(header), message);
    const tail = await blob.slice(-1024).text();
    const end = /startxref\s+(\d+)\s+%%EOF\s*$/.exec(tail);
    const offset = end && Number(end[1]);
    requireValue(Number.isSafeInteger(offset) && offset > 0 && offset < blob.size, message);
    // A proof can have a traditional cross-reference table or a PDF 1.5+ cross-reference stream.
    const section = blob.slice(offset);
    const xref = await section.slice(0, 4096).text();
    const table = /^xref\s+\d+\s+\d+\s+\d{10} \d{5} [fn]\b/.test(xref);
    if (table) {
      // Large tables or trailer dictionaries may extend beyond the EOF window.
      const header = /\btrailer\s*<</;
      const suffix = header.test(tail) ? tail : await section.text();
      const trailer = suffix.search(header);
      requireValue(trailer >= 0 && /\/(?:Root\s+\d+\s+\d+\s+R|Prev\s+\d+)\b/.test(suffix.slice(trailer)), message);
    } else {
      requireValue(/^\d+\s+\d+\s+obj\s*<</.test(xref), message);
      const stream = />>\s*stream[\r\n]/;
      const prefix = stream.test(xref) ? xref : await section.text();
      const dictionaryEnd = prefix.search(stream);
      requireValue(dictionaryEnd >= 0 && /\/Type\s*\/XRef\b/.test(prefix.slice(0, dictionaryEnd)) &&
        /endstream\s+endobj\b/.test(tail.slice(0, end.index)), message);
    }
    let loading;
    try {
      const pdfjs = await import("./pdf.min.mjs");
      pdfjs.GlobalWorkerOptions.workerSrc = new URL("./pdf.worker.min.mjs", assetBase).href;
      loading = pdfjs.getDocument({
        data: new Uint8Array(await blob.arrayBuffer()), isEvalSupported: false, stopAtErrors: true,
        cMapUrl: new URL("./cmaps/", assetBase).href, cMapPacked: true,
        standardFontDataUrl: new URL("./standard_fonts/", assetBase).href,
      });
      const pdf = await loading.promise;
      requireValue(pdf.numPages > 0, "교정 PDF에 지면이 없습니다.");
      const page = await pdf.getPage(1);
      // Drain the first page even for image-only proofs: a missing resource rejects this stream.
      await page.streamTextContent().pipeTo(new WritableStream());
    } catch (error) {
      if (error instanceof DocumentError) throw error;
      throw new DocumentError("교정 PDF의 첫 쪽을 읽을 수 없습니다.", {cause: error});
    } finally {
      await loading?.destroy();
    }
  }

  async function pack({source, style, includeProof, proof}, limits) {
    checkLimits(limits);
    requireValue(typeof source === "string" && isObject(style) && typeof includeProof === "boolean",
      "원고와 양식, 교정 PDF 포함 선택을 확인하세요.");
    requireValue(source.length <= limits.request && source.length <= limits.file, sizeError);
    try {
      const manifest = {format: "jjokgyeol", version: 1, includeProof};
      const files = new Map([
        ["manuscript.md", new Blob([source])],
        ["style.json", new Blob([JSON.stringify(style, storedNumber, 2), "\n"])],
      ]);
      if (includeProof && proof !== null) {
        manifest.proof = proofInfo(proof);
        requireValue(proof.blob instanceof Blob, "교정 PDF를 읽을 수 없습니다.");
        files.set(pdfName, proof.blob);
      }
      files.set("manifest.json", new Blob([JSON.stringify(manifest)]));
      let total = 0;
      for (const [name, blob] of files) total = entrySize(name, blob.size, total, limits);
      if (files.has(pdfName)) await checkPdf(files.get(pdfName));

      const writer = new zip.ZipWriter(new zip.BlobWriter("application/zip"), zipOptions);
      for (const [name, blob] of files) {
        await writer.add(name, new zip.BlobReader(blob), name === pdfName ? {level: 0} : {});
      }
      const file = await writer.close();
      requireValue(file.size <= limits.file, sizeError);
      return file;
    } catch (error) {
      if (error instanceof DocumentError) throw error;
      throw new DocumentError("쪽결 문서를 저장할 수 없습니다.", {cause: error});
    }
  }

  async function readText(entry, keepBom = false) {
    const bytes = await entry.getData(new zip.Uint8ArrayWriter());
    return new TextDecoder("utf-8", {fatal: true, ignoreBOM: keepBom}).decode(bytes);
  }

  async function unpack(file, limits) {
    checkLimits(limits);
    requireValue(file instanceof Blob && file.size <= limits.file, sizeError);
    const reader = new zip.ZipReader(new zip.BlobReader(file), {
      ...zipOptions, strictness: "strict", checkCrc32: true, checkOverlappingEntry: true,
    });
    try {
      const entries = new Map();
      let total = 0;
      for await (const entry of reader.getEntriesGenerator()) {
        const name = entry.filename;
        const fileType = (entry.unixMode ?? entry.unixExternalUpper) & 0o170000;
        requireValue((requiredNames.includes(name) || name === pdfName) && !entries.has(name) &&
          entry.rawFilename.length === name.length && entry.rawFilename.every((b, i) => b === name.charCodeAt(i)),
          "문서에 중복되거나 허용되지 않은 이름의 항목이 있습니다.");
        requireValue(!entry.directory && !entry.symlink && (!fileType || fileType === 0o100000) &&
          !entry.encrypted && [0, 8].includes(entry.compressionMethod), "지원하지 않는 ZIP 항목 형식입니다.");
        // zip.js enforces each declared size on its output stream and verifies CRC before getData resolves.
        total = entrySize(name, entry.uncompressedSize, total, limits);
        entries.set(name, entry);
      }
      requireValue(requiredNames.every(name => entries.has(name)), "문서에 필요한 항목이 없습니다.");
      const manifest = JSON.parse(await readText(entries.get("manifest.json")));
      requireValue(isObject(manifest) && manifest.format === "jjokgyeol" && manifest.version === 1,
        "지원하지 않는 쪽결 문서 형식 또는 버전입니다.");
      const {includeProof} = manifest;
      const hasPdf = entries.has(pdfName);
      requireValue(typeof includeProof === "boolean" && hasPdf === (manifest.proof != null) &&
        (!hasPdf || includeProof), "교정 PDF와 문서의 포함 정보가 일치하지 않습니다.");
      const info = hasPdf ? proofInfo(manifest.proof) : null;
      const style = JSON.parse(await readText(entries.get("style.json")), storedNumber);
      requireValue(isObject(style), "문서의 양식은 JSON 객체여야 합니다.");
      const source = await readText(entries.get("manuscript.md"), true);
      let proof = null;
      if (info) {
        const blob = await entries.get(pdfName).getData(new zip.BlobWriter("application/pdf"));
        await checkPdf(blob);
        proof = {blob, ...info};
      }
      return {source, style, includeProof, proof};
    } catch (error) {
      if (error instanceof DocumentError) throw error;
      throw new DocumentError("쪽결 문서가 손상되었거나 내용을 읽을 수 없습니다.", {cause: error});
    } finally {
      await reader.close();
    }
  }

  return {pack, unpack};
})();
