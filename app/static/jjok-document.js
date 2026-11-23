"use strict";

includeProof.addEventListener("change", editedDocumentOption);

function documentError(error, message) {
  if (error instanceof TypeError || error instanceof SyntaxError) return message;
  return error.message ? message + " " + error.message : message;
}

byId("save-document").addEventListener("click", async () => {
  if (savingDocument !== null || fileLoads.source || fileLoads.style) return;
  const identity = documentIdentity;
  const revision = documentRevision;
  const savedVersion = version;
  const name = documentFilename || byId("filename").textContent.replace(/\.(md|markdown)$/i, "") + ".jjok";
  const source = manuscriptFileText();
  const values = styleForm.get();
  const style = JSON.stringify(values);
  const include = includeProof.checked;
  const selected = include && displayedDocument === identity && displayedProof?.selected;
  const proof = selected ? {
    blob: selected.blob, mode: selected.mode, state: displayedVersion === version ? "current" : "stale",
  } : null;
  const reportError = error => {
    if (identity !== documentIdentity) return;
    errorText.textContent = documentError(error, "쪽결 문서를 저장하지 못했습니다.");
  };

  savingDocument = identity;
  updateFileActions();
  errorText.textContent = "";
  try {
    const [stored, fonts] = await Promise.all([requestStyle("export", style), documentFonts.archiveFonts(values)]);
    const archive = await JjokArchive.pack({source, style: stored, includeProof: include, proof, fonts},
      settings.document_limits, settings.font_assets);
    const written = await saveBlob(archive, name, reportError);
    if (written === false || identity !== documentIdentity) return;
    documentFilename = name;
    if (written && savedVersion === version && revision === documentRevision) {
      markDirty("source", false);
      markDirty("style", false);
      markDirty("document", false);
    }
  } catch (error) {
    reportError(error);
  } finally {
    savingDocument = null;
    updateFileActions();
  }
});

function clearDocumentProof() {
  byId("desktop-pages")?.remove();
  preview.removeAttribute("src");
  preview.hidden = true;
  byId("viewer-fallback").hidden = true;
  byId("placeholder").hidden = false;
  byId("proof-size").textContent = "PDF";
  byId("applied-ratios").textContent = "";
  byId("proof-decision").textContent = "";
  displayedProof?.release();
  displayedProof = null;
  displayedVersion = displayedDocument = -1;
  clearSavedChoice();
}

async function confirmDocumentOpen(current) {
  if (!nativeDesktop) return confirmWebReplacement();
  return await confirmManuscriptReplacement(current) && current() && await confirmStyleReplacement(current);
}

bindFilePicker("open-document", "document-file", async file => {
  const turn = beginFileOpen("document");
  const current = () => fileLoads.document === turn;
  initialPreviewNeeded = false;
  try {
    const {source, style, includeProof: include, proof, fonts} = await JjokArchive.unpack(file, settings.document_limits, settings.font_assets);
    if (!current()) return;
    const [values, records] = await Promise.all([requestStyle("open", JSON.stringify(style)), documentFonts.readArchive(fonts)]);
    if (!current()) return;
    const pages = nativeDesktop && proof ? await desktop.pages(proof.blob) : null;
    if (!current() || !await confirmDocumentOpen(current) || !current()) return;
    const restored = proof && new ProofUrls({saved: proof});
    clearDocumentProof();
    applyManuscript(source, file.name.replace(/\.jjok$/i, "") + ".md");
    includeProof.checked = include;
    documentFonts.restore(records);
    styleForm.set(values);
    styleRevision++;
    markDirty("style", false);
    changed();
    documentFilename = file.name;
    if (restored) {
      if (pages) preview.parentNode.append(pages);
      const state = documentFonts.updateFallback(values) ? "stale" : proof.state;
      showProof(restored, state === "current" ? values : null, state);
    }
  } catch (error) {
    if (current()) errorText.textContent = documentError(error, "쪽결 문서를 열지 못했습니다.");
  } finally {
    finishFileOpen("document", turn);
  }
});
