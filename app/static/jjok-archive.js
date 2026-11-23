"use strict";

globalThis.JjokArchive = (() => {
  // @zip.js/zip.js 2.25.0, dist/zip-native.min.js. Its JS fallback also runs offline.
  const zipOptions = {useWebWorkers: false};
  const requiredNames = ["manifest.json", "style.json", "manuscript.md"];
  const pdfName = "proof.pdf";
  const fontPath = /^fonts\/[0-9a-f]{64}$/;
  const noticePath = /^notices\/[0-9a-f]{64}\.txt$/;
  const pdfJsStreamError = 5; // StreamKind.ERROR in the pinned PDF.js 4.10.38 worker.
  const assetBase = document.currentScript.src;
  const sizeError = "문서가 허용 크기를 초과합니다.";
  const fontError = "글꼴의 참조와 원본 파일 정보가 일치하지 않습니다.";
  const noticeError = "글꼴의 배포 고지를 확인할 수 없습니다.";

  class DocumentError extends Error {}

  function requireValue(condition, message) {
    if (!condition) throw new DocumentError(message);
  }

  function checkLimits(limits) {
    const positive = n => Number.isSafeInteger(n) && n > 0;
    requireValue(positive(limits?.request) && positive(limits?.file) &&
      ["pdf", "font_count", "font_file", "font_total"].every(k => limits[k] === undefined || positive(limits[k])),
      "문서 크기 설정을 확인하세요.");
  }

  function entryBudget(limits) {
    let total = 0, fontTotal = 0, fontCount = 0, noticeCount = 0;
    return (name, size) => {
      requireValue(Number.isSafeInteger(size) && size >= 0 && size <= limits.file - total, sizeError);
      total += size;
      if (fontPath.test(name)) {
        requireValue(++fontCount <= limits.font_count && size <= limits.font_file &&
          size <= limits.font_total - fontTotal, sizeError);
        fontTotal += size;
      } else if (name === pdfName) {
        requireValue(size <= (limits.pdf ?? limits.file), sizeError);
      } else {
        requireValue(size <= limits.request, sizeError);
        if (noticePath.test(name)) requireValue(++noticeCount <= limits.font_count, sizeError);
      }
    };
  }

  function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function installedAssets() {
    return typeof settings === "undefined" ? undefined : settings.font_assets;
  }

  function checkFontCount(count, limits) {
    requireValue(count <= (limits.font_count ?? 0), "글꼴 개수가 허용 범위를 초과합니다.");
  }

  function checkStyleFonts(style, hashes) {
    for (const id of documentFontIds(style)) {
      const pair = documentFontReference.exec(id).slice(1);
      requireValue(pair.every(hash => hash === "none" || hashes.has(hash)), fontError);
    }
  }

  function isHash(value) {
    return typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
  }

  function fontReference(font) {
    requireValue(isObject(font), fontError);
    const {hash, name, version, weight} = font;
    requireValue(isHash(hash) &&
      typeof name === "string" && typeof version === "string" && ["regular", "bold"].includes(weight), fontError);
    return {hash, name, version, weight};
  }

  async function hashedNotice(blob, limits) {
    requireValue(blob instanceof Blob, noticeError);
    requireValue(blob.size <= limits.request && blob.size <= limits.file, sizeError);
    const hash = await FontRights.sha256(await blob.arrayBuffer());
    return {hash, blob};
  }

  async function verifiedNotice(hash, face, supplied, known, limits) {
    requireValue(face.hash === hash, fontError);
    if (face.archive !== true) return null;
    if (known?.faces?.[hash]) {
      requireValue(supplied && supplied.hash === known.notice, noticeError);
      return supplied;
    }
    const canonical = await hashedNotice(await FontRights.notice(face), limits);
    requireValue(!supplied || supplied.hash === canonical.hash, noticeError);
    return supplied ?? canonical;
  }

  function storedNumber(key, value) {
    requireValue(typeof value !== "number" || Number.isSafeInteger(value), "양식의 숫자는 정수여야 합니다.");
    return value;
  }

  function proofInfo(proof, version) {
    requireValue(isObject(proof) && ["current", "stale"].includes(proof.state) &&
      ["rule", "model", "mixed"].includes(proof.mode), "교정 PDF의 상태와 조판 방식을 확인하세요.");
    const info = {state: proof.state, mode: proof.mode};
    if (version === 2 && proof.appliedFonts !== undefined) {
      requireValue(isObject(proof.appliedFonts) && Object.entries(proof.appliedFonts).every(([id, pair]) =>
        documentFontReference.test(id) && Array.isArray(pair) && pair.length === 2 &&
        pair.every(hash => hash === null || isHash(hash))), fontError);
      info.appliedFonts = proof.appliedFonts;
    }
    return info;
  }

  async function checkDrawing(page, port) {
    const side = 64;
    const {width, height} = page.getViewport({scale: 1});
    const viewport = page.getViewport({scale: side / Math.max(width, height)});
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = side;
    let task;
    // render() can resolve after this document’s worker has rejected the drawing stream.
    const cancelFailedDrawing = ({data}) => {
      if (data.stream === pdfJsStreamError) task?.cancel();
    };
    port.addEventListener("message", cancelFailedDrawing);
    try {
      task = page.render({canvasContext: canvas.getContext("2d"), viewport});
      await task.promise;
    } finally {
      port.removeEventListener("message", cancelFailedDrawing);
      canvas.width = canvas.height = 0;
    }
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
    let worker, loading;
    try {
      const pdfjs = await import("./pdf.min.mjs");
      pdfjs.GlobalWorkerOptions.workerSrc = new URL("./pdf.worker.min.mjs", assetBase).href;
      worker = new pdfjs.PDFWorker();
      loading = pdfjs.getDocument({
        worker,
        data: new Uint8Array(await blob.arrayBuffer()), isEvalSupported: false, stopAtErrors: true,
        cMapUrl: new URL("./cmaps/", assetBase).href, cMapPacked: true,
        standardFontDataUrl: new URL("./standard_fonts/", assetBase).href,
      });
      const pdf = await loading.promise;
      requireValue(pdf.numPages > 0, "교정 PDF에 지면이 없습니다.");
      const page = await pdf.getPage(1);
      // Drain the first page even for image-only proofs: a missing resource rejects this stream.
      await page.streamTextContent().pipeTo(new WritableStream());
      await checkDrawing(page, worker.port);
    } catch (error) {
      if (error instanceof DocumentError) throw error;
      throw new DocumentError("교정 PDF의 첫 쪽을 읽을 수 없습니다.", {cause: error});
    } finally {
      try { await loading?.destroy(); } finally { worker?.destroy(); }
    }
  }

  async function packFonts(style, fonts, files, limits, known) {
    requireValue(Array.isArray(fonts), fontError);
    const records = new Map(), fileSizes = new Map();
    const charge = entryBudget(limits);
    const inputs = [];
    for (const font of fonts) {
      const ref = fontReference(font);
      const previous = records.get(ref.hash);
      requireValue(!previous || (previous.name === ref.name && previous.version === ref.version &&
        previous.weight === ref.weight), fontError);
      if (!previous) {
        checkFontCount(records.size + 1, limits);
        records.set(ref.hash, ref);
      }
      const {file, notice} = font;
      if (file === undefined) {
        requireValue(notice === undefined, noticeError);
        continue;
      }
      requireValue(file instanceof Blob, fontError);
      if (notice !== undefined) {
        requireValue(notice instanceof Blob, noticeError);
        requireValue(notice.size <= limits.request && notice.size <= limits.file, sizeError);
      }
      if (fileSizes.has(ref.hash)) {
        requireValue(fileSizes.get(ref.hash) === file.size, fontError);
      } else {
        charge("fonts/" + ref.hash, file.size);
        fileSizes.set(ref.hash, file.size);
      }
      inputs.push({ref: previous ?? ref, file, notice});
    }
    checkStyleFonts(style, records);
    const faces = new Map(), notices = new Map();
    for (const {ref, file, notice} of inputs) {
      if (!faces.has(file)) faces.set(file, await FontRights.inspect(file, known ?? {}));
      if (notice && !notices.has(notice)) notices.set(notice, await hashedNotice(notice, limits));
      const verified = await verifiedNotice(ref.hash, faces.get(file), notices.get(notice), known, limits);
      if (!verified) continue;
      ref.file = "fonts/" + ref.hash;
      ref.notice = "notices/" + verified.hash + ".txt";
      files.set(ref.file, file);
      files.set(ref.notice, verified.blob);
    }
    return [...records.values()];
  }

  async function pack({source, style, includeProof, proof, fonts = []}, limits, known = installedAssets()) {
    checkLimits(limits);
    requireValue(typeof source === "string" && isObject(style) && typeof includeProof === "boolean",
      "원고와 양식, 교정 PDF 포함 선택을 확인하세요.");
    requireValue(source.length <= limits.request && source.length <= limits.file, sizeError);
    try {
      const manifest = {format: "jjokgyeol", version: 2, includeProof};
      const files = new Map([
        ["manuscript.md", new Blob([source])],
        ["style.json", new Blob([JSON.stringify(style, storedNumber, 2), "\n"])],
      ]);
      if (includeProof && proof !== null) {
        manifest.proof = proofInfo(proof, manifest.version);
        requireValue(proof.blob instanceof Blob, "교정 PDF를 읽을 수 없습니다.");
        files.set(pdfName, proof.blob);
      }
      manifest.fonts = await packFonts(style, fonts, files, limits, known);
      files.set("manifest.json", new Blob([JSON.stringify(manifest)]));
      const charge = entryBudget(limits);
      for (const [name, blob] of files) charge(name, blob.size);
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

  function declaredFonts(manifest, style, entries, limits) {
    const fonts = manifest.version === 1 ? [] : manifest.fonts;
    requireValue(Array.isArray(fonts), fontError);
    checkFontCount(fonts.length, limits);
    const hashes = new Set();
    const expected = new Set(requiredNames);
    if (entries.has(pdfName)) expected.add(pdfName);
    const records = fonts.map(font => {
      const ref = fontReference(font);
      requireValue(!hashes.has(ref.hash), "문서에 같은 글꼴이 두 번 선언되어 있습니다.");
      hashes.add(ref.hash);
      if ("file" in font || "notice" in font) {
        requireValue(font.file === "fonts/" + ref.hash && typeof font.notice === "string" &&
          noticePath.test(font.notice), fontError);
        expected.add(font.file);
        expected.add(font.notice);
        return {...ref, file: font.file, notice: font.notice};
      }
      return ref;
    });
    if (manifest.version === 2) checkStyleFonts(style, hashes);
    requireValue(entries.size === expected.size && [...expected].every(name => entries.has(name)),
      "문서의 글꼴 목록과 ZIP 항목이 일치하지 않습니다.");
    return records;
  }

  async function unpackFonts(records, entries, known, limits) {
    const fonts = [], notices = new Map();
    for (const {file, notice, ...ref} of records) {
      if (file) {
        if (!notices.has(notice)) {
          const blob = await entries.get(notice).getData(new zip.BlobWriter("text/plain"));
          const supplied = await hashedNotice(blob, limits);
          requireValue(notice === "notices/" + supplied.hash + ".txt", noticeError);
          notices.set(notice, supplied);
        }
        const blob = await entries.get(file).getData(new zip.BlobWriter());
        const supplied = notices.get(notice);
        // Earlier v2 writers treated these additional bundled faces as external OFL fonts.
        const oldNotice = known?.faces?.[ref.hash]?.fallback === false && supplied.hash !== known.notice;
        const catalog = oldNotice ? {} : known;
        const face = await FontRights.inspect(blob, catalog ?? {});
        const verified = await verifiedNotice(ref.hash, face, supplied, catalog, limits);
        requireValue(!oldNotice || verified, noticeError);
        if (verified) Object.assign(ref, {file: blob, notice: verified.blob});
      }
      fonts.push(ref);
    }
    return fonts;
  }

  async function unpack(file, limits, known = installedAssets()) {
    checkLimits(limits);
    requireValue(file instanceof Blob && file.size <= limits.file, sizeError);
    const reader = new zip.ZipReader(new zip.BlobReader(file), {
      ...zipOptions, strictness: "strict", checkCrc32: true, checkOverlappingEntry: true,
    });
    try {
      const entries = new Map();
      const charge = entryBudget(limits);
      for await (const entry of reader.getEntriesGenerator()) {
        const name = entry.filename;
        const fileType = (entry.unixMode ?? entry.unixExternalUpper) & 0o170000;
        requireValue((requiredNames.includes(name) || name === pdfName || fontPath.test(name) || noticePath.test(name)) &&
          !entries.has(name) &&
          entry.rawFilename.length === name.length && entry.rawFilename.every((b, i) => b === name.charCodeAt(i)),
          "문서에 중복되거나 허용되지 않은 이름의 항목이 있습니다.");
        requireValue(!entry.directory && !entry.symlink && (!fileType || fileType === 0o100000) &&
          !entry.encrypted && [0, 8].includes(entry.compressionMethod), "지원하지 않는 ZIP 항목 형식입니다.");
        // zip.js enforces each declared size on its output stream and verifies CRC before getData resolves.
        charge(name, entry.uncompressedSize);
        entries.set(name, entry);
      }
      requireValue(requiredNames.every(name => entries.has(name)), "문서에 필요한 항목이 없습니다.");
      const manifest = JSON.parse(await readText(entries.get("manifest.json")));
      requireValue(isObject(manifest) && manifest.format === "jjokgyeol" && [1, 2].includes(manifest.version),
        "지원하지 않는 쪽결 문서 형식 또는 버전입니다.");
      const {includeProof} = manifest;
      const hasPdf = entries.has(pdfName);
      requireValue(typeof includeProof === "boolean" && hasPdf === (manifest.proof != null) &&
        (!hasPdf || includeProof), "교정 PDF와 문서의 포함 정보가 일치하지 않습니다.");
      const info = hasPdf ? proofInfo(manifest.proof, manifest.version) : null;
      const style = JSON.parse(await readText(entries.get("style.json")), storedNumber);
      requireValue(isObject(style), "문서의 양식은 JSON 객체여야 합니다.");
      const records = declaredFonts(manifest, style, entries, limits);
      if (manifest.version === 2 && info && info.appliedFonts === undefined && documentFontIds(style).length) {
        info.state = "stale";
      }
      const source = await readText(entries.get("manuscript.md"), true);
      const fonts = await unpackFonts(records, entries, known, limits);
      let proof = null;
      if (info) {
        const blob = await entries.get(pdfName).getData(new zip.BlobWriter("application/pdf"));
        await checkPdf(blob);
        proof = {blob, ...info};
      }
      return {source, style, includeProof, proof, fonts};
    } catch (error) {
      if (error instanceof DocumentError) throw error;
      throw new DocumentError("쪽결 문서가 손상되었거나 내용을 읽을 수 없습니다.", {cause: error});
    } finally {
      await reader.close();
    }
  }

  return {pack, unpack};
})();
