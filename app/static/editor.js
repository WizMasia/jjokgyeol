"use strict";

const byId = id => document.getElementById(id);
const source = byId("source");
const preview = byId("preview");
const proofChoice = byId("proof-choice");
const nativeDesktop = typeof desktop !== "undefined";
const settingsView = nativeDesktop && new URLSearchParams(location.search).get("view") === "settings";
if (settingsView) document.documentElement.classList.add("settings-window");
const settingsDialog = byId("settings-dialog");
const settingsError = byId("settings-error");
let nativePreference = "model";
function liveProofChoices(preferred = "model", allowModel = !nativeDesktop) {
  proofChoice.replaceChildren();
  if (allowModel) proofChoice.add(new Option("모델 우선", "model"));
  proofChoice.add(new Option("규칙", "rule"));
  proofChoice.value = allowModel ? preferred : "rule";
  proofChoice.disabled = false;
}
liveProofChoices();
const inlinePdf = navigator.pdfViewerEnabled !== false;
// A viewerless browser would download every iframe navigation automatically.
if (!inlinePdf) preview.setAttribute("sandbox", "");
const statusText = byId("status");
const errorText = byId("error");
let version = 0;
let displayedVersion = -1;
let displayedProof = null;
let documentIdentity = 0;
let displayedDocument = -1;
let documentRevision = 0;
let documentDirty = false;
let documentFilename = null;
let savingDocument = null;
const includeProof = byId("include-proof");
let pending = null;
const fileLoads = {
  source: null, style: null,
  get document() { return this.source === this.style ? this.source : null; },
};
let initialPreviewNeeded = false;
let original = "";
let originalView = "";
let withBom = false;
let newline = "\n";
let mixedEndings = false;
// Textarea undo retains LF text only. Keep the raw changes for mixed-ending formatting edits.
const alignmentUndo = [];
const alignmentRedo = [];
let editStart = Infinity;
let manuscriptDirty = false;
let styleDirty = false;
let manuscriptRevision = 0;
let styleRevision = 0;
const dirtyUpdates = {
  source: {pending: 0, latest: Promise.resolve(true)},
  style: {pending: 0, latest: Promise.resolve(true)},
};

function queueDirtyUpdate(kind, dirty) {
  const queue = dirtyUpdates[kind];
  const send = () => kind === "source" ? desktop.setDirty(dirty) : desktop.setStyleDirty(dirty);
  const update = queue.pending ? queue.latest.then(send) : send();
  queue.pending++;
  queue.latest = Promise.resolve(update).then(() => true, error => {
    errorText.textContent = error.message;
    return false;
  }).finally(() => { queue.pending--; });
  return queue.latest;
}

async function waitForDirtyUpdate(kind) {
  const queue = dirtyUpdates[kind];
  while (true) {
    const latest = queue.latest;
    const succeeded = await latest;
    if (latest !== queue.latest) continue;
    if (succeeded) return true;
    const dirty = kind === "source" ? manuscriptDirty || documentDirty : styleDirty;
    const retry = queueDirtyUpdate(kind, dirty);
    if (!await retry && retry === queue.latest) return false;
  }
}

function markDirty(kind, dirty) {
  const slot = kind === "style" ? "style" : "source";
  const before = slot === "source" ? manuscriptDirty || documentDirty : styleDirty;
  if (kind === "source") manuscriptDirty = dirty;
  else if (kind === "document") documentDirty = dirty;
  else styleDirty = dirty;
  const after = slot === "source" ? manuscriptDirty || documentDirty : styleDirty;
  if (before !== after && nativeDesktop && !settingsView) queueDirtyUpdate(slot, after);
}

function hasActiveArchive() {
  return documentFilename !== null || savingDocument === documentIdentity;
}

function confirmWebReplacement() {
  return !(manuscriptDirty || styleDirty || documentDirty) ||
    window.confirm("저장하지 않은 변경 사항이 있습니다. 현재 문서를 바꿀까요?");
}

async function confirmManuscriptReplacement(current) {
  if (!nativeDesktop) return !hasActiveArchive() || confirmWebReplacement();
  try {
    if (!await waitForDirtyUpdate("source") || !current()) return false;
    if (!manuscriptDirty && !documentDirty) return true;
    return await desktop.confirmDocumentReplacement();
  } catch (error) {
    if (current()) errorText.textContent = error.message;
    return false;
  }
}

async function confirmStyleReplacement(current) {
  if (!nativeDesktop) return true;
  try {
    if (settingsView) return !styleDirty || await desktop.invoke("confirm_settings_replacement");
    if (!await waitForDirtyUpdate("style") || !current()) return false;
    if (!styleDirty) return true;
    return await desktop.confirmStyleReplacement();
  } catch (error) {
    if (current()) errorText.textContent = error.message;
    return false;
  }
}

function rememberSource(text) {
  original = text;
  originalView = source.value;
  newline = text.match(/\r\n|\r|\n/)?.[0] ?? "\n";
  mixedEndings = /[\r\n]/.test(text.replaceAll(newline, ""));
}

function editedRange(before, after, prefixLimit = Infinity) {
  let start = 0;
  let end = before.length;
  let nextEnd = after.length;
  while (start < Math.min(end, nextEnd, prefixLimit) && before[start] === after[start]) start++;
  while (end > start && nextEnd > start && before[end - 1] === after[nextEnd - 1]) {
    end--;
    nextEnd--;
  }
  return {start, end, nextEnd};
}

// Only CRLF occupies a second code unit in the file at a textarea newline.
function rawSourceRange(text, start, end) {
  for (const match of text.matchAll(/\r\n/g)) {
    if (match.index >= end) break;
    if (match.index < start) start++;
    end++;
  }
  return [start, end];
}

function sourceText() {
  if (source.value === originalView) return original;
  return source.value.replaceAll("\n", newline);
}

function manuscriptFileText() {
  return (withBom ? "\uFEFF" : "") + sourceText();
}

function restoreAlignmentEndings(history, next) {
  const undo = history === alignmentUndo;
  const patch = history.at(-1);
  if (!patch) return false;
  const from = undo ? patch.after : patch.before;
  const to = undo ? patch.before : patch.after;
  if (!original.startsWith(from, patch.start)) return false;
  const restored = original.slice(0, patch.start) + to + original.slice(patch.start + from.length);
  if (restored.replace(/\r\n?/g, "\n") !== source.value) return false;
  rememberSource(restored);
  next.push(history.pop());
  return true;
}

function editManuscript(event) {
  let restored = false;
  if (event.inputType === "historyUndo") restored = restoreAlignmentEndings(alignmentUndo, alignmentRedo);
  else if (event.inputType === "historyRedo") restored = restoreAlignmentEndings(alignmentRedo, alignmentUndo);
  else alignmentRedo.length = 0;
  if (!restored && (mixedEndings || alignmentUndo.length || alignmentRedo.length)) {
    const view = source.value;
    const {start, end, nextEnd} = editedRange(originalView, view, Math.min(editStart, source.selectionStart));
    const [rawStart, rawEnd] = rawSourceRange(original, start, end);
    original = original.slice(0, rawStart) + view.slice(start, nextEnd).replaceAll("\n", newline) + original.slice(rawEnd);
    originalView = view;
  }
  editStart = Infinity;
  edited("source");
}

const settings = JSON.parse(byId("editor-settings").textContent);
let decisionConnection = {provider: "rule"};
let connectionRevision = 0;
const connectionForm = decisionSettings(settings.decision_providers ?? [], settings.builtin_decision,
  !nativeDesktop && settings.operator_decision);
const stylePanel = byId("style");
const marginMode = byId("margin-mode");
marginMode.add(new Option("고정 좌우 여백", "fixed"));
marginMode.add(new Option("양면 여백", "mirrored"));
const gutter = byId("gutter-mm");
const margins = stylePanel.querySelectorAll('[name="margins_mm"]');
const [pageWidth, pageHeight] = stylePanel.querySelectorAll('[name="page_mm"]');
const pagePreset = byId("page-preset");
const pagePresets = settings.page_presets_mm ?? {};
for (const [name, size] of Object.entries(pagePresets)) {
  pagePreset.add(new Option(`${name} (${size.join(" × ")} mm)`, name));
}
pagePreset.add(new Option("직접 입력", "custom"));
let marginSplit;
const indentKeys = ["first_indent_mm", "rest_indent_mm"];
const indents = indentEditor(settings.indent_units, indentKeys);
const listStyle = listLevelEditor(settings, () => edited("style"));
const lineNumbers = byId("line-numbers");
const oddLineNumbers = byId("odd-line-numbers");
const evenLineNumbers = byId("even-line-numbers");
const lineNumberFaces = byId("line-number-faces");
for (const select of [lineNumbers, oddLineNumbers, evenLineNumbers]) {
  select.add(new Option("끔", "off"));
  select.add(new Option("왼쪽", "left"));
  select.add(new Option("오른쪽", "right"));
}
lineNumbers.add(new Option("홀짝 따로 지정", "separate"));
lineNumbers.addEventListener("input", () => {
  lineNumberFaces.hidden = lineNumbers.value !== "separate";
  if (lineNumberFaces.hidden) {
    oddLineNumbers.value = lineNumbers.value;
    evenLineNumbers.value = lineNumbers.value;
  }
});
const articleNumbering = byId("article-numbering");
const articleExpr = byId("article-expr");
const pinButton = byId("toggle-article-pin");
const legalOptions = byId("legal-options");
const articleStyle = byId("article-style");
legalOptions.hidden = typeof settings.default_article_expr !== "string";

function showLegalOptions(open) {
  articleStyle.hidden = !open || legalOptions.hidden;
  legalOptions.setAttribute("aria-expanded", String(!articleStyle.hidden));
  pinButton.hidden = legalOptions.hidden || (articleStyle.hidden && !articleNumbering.checked);
}

legalOptions.addEventListener("click", () => showLegalOptions(articleStyle.hidden));

function articleError(message) {
  if (message) showLegalOptions(true);
  articleExpr.setAttribute("aria-invalid", String(Boolean(message)));
  byId("article-expr-error").textContent = message;
}

function syncPagePreset() {
  const match = Object.entries(pagePresets).find(([, [width, height]]) =>
    sameDecimal(pageWidth.value, width) && sameDecimal(pageHeight.value, height));
  pagePreset.value = match?.[0] ?? "custom";
}

function matchesSplit() {
  return marginSplit && [1, 3].every(i => sameDecimal(margins[i].value, marginSplit.fixed.margins_mm[i]));
}

const marginFormat = new Intl.NumberFormat("ko-KR", {maximumFractionDigits: 6, useGrouping: false});
function updateMarginSummary() {
  if (gutter.disabled) return;
  const values = [margins[3].value, gutter.value, margins[1].value, pageWidth.value];
  const output = byId("margin-summary");
  const [inner, binding, outer, width] = values.map(Number);
  const actual = inner + binding;
  output.textContent = values.every(value => decimalKey(value) !== null) && [actual, outer, width].every(Number.isFinite)
    ? `실제 안쪽 ${marginFormat.format(actual)} mm · 본문 폭 ${marginFormat.format(width - actual - outer)} mm` : "";
}

function setMarginMode(mirrored, value = "") {
  gutter.value = value;
  gutter.disabled = !mirrored;
  marginMode.value = mirrored ? "mirrored" : "fixed";
  byId("gutter-control").hidden = !mirrored;
  byId("left-margin-label").textContent = mirrored ? "안쪽" : "왼쪽";
  byId("right-margin-label").textContent = mirrored ? "바깥쪽" : "오른쪽";
  updateMarginSummary();
  marginError();
}

function marginError(field, message) {
  const output = byId("margin-error");
  const index = /^margins_mm\[(\d+)\]$/.exec(field);
  const fields = field === "gutter_mm" && !gutter.disabled ? [gutter] : field === "margins_mm" ? margins
    : index && margins[Number(index[1])] ? [margins[Number(index[1])]] : [];
  for (const control of [gutter, ...margins]) {
    control.removeAttribute("aria-invalid");
    control.removeAttribute("aria-describedby");
  }
  for (const control of fields) {
    control.setAttribute("aria-invalid", "true");
    control.setAttribute("aria-describedby", output.id);
  }
  output.textContent = fields.length ? message : "";
  if (fields.length) stylePanel.querySelector("details").open = true;
}

function showStyleFieldError(field, message, {clearMargin = false} = {}) {
  if (field === "article_expr") articleError(message);
  if (clearMargin || /^(gutter_mm|margins_mm(?:\[\d+\])?)$/.test(field)) marginError(field, message);
  typography.error(field, message);
}

function marginGeometry(values) {
  const {page_mm, margins_mm, first_indent_mm, rest_indent_mm, gutter_mm} = values;
  const geometry = {page_mm, margins_mm, first_indent_mm, rest_indent_mm, gutter_mm};
  const rules = (values.section_rules ?? []).flatMap(rule => {
    const fields = Object.fromEntries(indentKeys.filter(key => Object.hasOwn(rule.values, key))
      .map(key => [key, rule.values[key]]));
    return Object.keys(fields).length ? [{path: rule.path, role: rule.role, values: fields}] : [];
  });
  if (rules.length) geometry.section_rules = rules;
  return geometry;
}

function applyMarginGeometry(geometry) {
  pageWidth.value = geometry.page_mm[0];
  syncPagePreset();
  for (const i of [1, 3]) margins[i].value = geometry.margins_mm[i];
  indents.set(geometry, true);
  let index = 0;
  sectionRules = sectionRules?.map(rule => indentKeys.some(key => Object.hasOwn(rule.values, key))
    ? {...rule, values: {...rule.values, ...geometry.section_rules[index++].values}} : rule);
  setMarginMode(geometry.gutter_mm !== undefined, geometry.gutter_mm);
}

function exportMarginGeometry(geometry) {
  return requestStyle("export", JSON.stringify(geometry));
}

async function fixedMargins(mirrored) {
  const {gutter_sp, ...stored} = await exportMarginGeometry(mirrored);
  stored.margins_sp[3] += gutter_sp;
  const fixed = await requestStyle("open", JSON.stringify(stored));
  const draft = {...mirrored, gutter_mm: undefined, margins_mm: [...mirrored.margins_mm]};
  draft.margins_mm[3] = fixed.margins_mm[3];
  try {
    await exportMarginGeometry(draft);
    return {fixed: draft, gutter_sp};
  } catch (error) {
    if (error.status !== 422) throw error;
    return {fixed: marginGeometry(fixed), gutter_sp};
  }
}

function restoredMarginDraft(current, {raw, fixed}) {
  const restore = (value, canonical, original) => sameDecimal(value, canonical) ? original : value;
  const rulesUnchanged = JSON.stringify(current.section_rules) === JSON.stringify(fixed.section_rules);
  return {
    ...current,
    page_mm: [restore(current.page_mm[0], fixed.page_mm[0], raw.page_mm[0]), current.page_mm[1]],
    margins_mm: [current.margins_mm[0], raw.margins_mm[1], current.margins_mm[2], raw.margins_mm[3]],
    gutter_mm: raw.gutter_mm,
    first_indent_mm: restore(current.first_indent_mm, fixed.first_indent_mm, raw.first_indent_mm),
    rest_indent_mm: restore(current.rest_indent_mm, fixed.rest_indent_mm, raw.rest_indent_mm),
    section_rules: rulesUnchanged ? raw.section_rules : current.section_rules,
  };
}

async function validatedMarginRestore(draft, current, gutterSp) {
  try {
    await exportMarginGeometry(draft);
    return draft;
  } catch (error) {
    if (error.status !== 422) throw error;
  }
  // A new indent can fit the fixed layout at 1sp but exceed the restored raw decimals.
  let stored;
  try {
    stored = await exportMarginGeometry(current);
  } catch (error) {
    if (error.status !== 422) throw error;
    return draft; // Leave an already-invalid edit available in either mode.
  }
  stored.margins_sp[3] -= gutterSp;
  stored.gutter_sp = gutterSp;
  return marginGeometry(await requestStyle("open", JSON.stringify(stored)));
}

const documentFonts = documentFontEditor(settings);
const typography = typographyEditor(settings);
let sectionRules;
const styleFields = Object.keys(settings.defaults).filter(name => ![...indentKeys, "list_levels", "article_expr", "section_rules", "gutter_mm", "text_styles", "ko_tracking_bounds_pct", "paragraph_layout"].includes(name)).map(name => [
  name, stylePanel.querySelectorAll(`[name="${CSS.escape(name)}"]`),
]);
const styleForm = {
  get() {
    const values = Object.fromEntries(styleFields.map(([name, controls]) => [
      name, controls.length === 1 ? controls[0].value : Array.from(controls, control => control.value),
    ]));
    const levels = listStyle.get();
    if (levels !== undefined) values.list_levels = levels;
    if (lineNumbers.value === "separate") {
      values.line_numbers = {odd: oddLineNumbers.value, even: evenLineNumbers.value};
    } else if (lineNumbers.value !== "off") {
      values.line_numbers = lineNumbers.value;
    }
    if (articleNumbering.checked) values.article_expr = articleExpr.value;
    if (sectionRules !== undefined) values.section_rules = structuredClone(sectionRules);
    if (!gutter.disabled) values.gutter_mm = gutter.value;
    if (byId("paragraph-gap-pt").value !== "") values.paragraph_gap_pt = byId("paragraph-gap-pt").value;
    else delete values.paragraph_gap_pt;
    return Object.assign(values, indents.get(), typography.get());
  },
  set(values, {preserveDraft = false, inactiveArticleExpr = settings.default_article_expr ?? ""} = {}) {
    documentFonts.refreshChoices(values);
    for (const [name, controls] of styleFields) {
      const value = values[name];
      controls.forEach((control, index) => {
        control.value = Array.isArray(value) ? value[index] : value;
      });
    }
    byId("paragraph-gap-pt").value = values.paragraph_gap_pt ?? "";
    syncPagePreset();
    indents.set(values, preserveDraft);
    if (!preserveDraft || !matchesSplit()) marginSplit = undefined;
    setMarginMode(values.gutter_mm !== undefined, values.gutter_mm);
    sectionRules = structuredClone(values.section_rules);
    typography.set(values, preserveDraft);
    listStyle.set(values.list_levels);
    const numbering = values.line_numbers ?? "off";
    const separate = typeof numbering === "object";
    lineNumbers.value = separate ? "separate" : numbering;
    oddLineNumbers.value = separate ? numbering.odd : numbering;
    evenLineNumbers.value = separate ? numbering.even : numbering;
    lineNumberFaces.hidden = !separate;
    articleNumbering.checked = values.article_expr !== undefined;
    articleExpr.disabled = !articleNumbering.checked;
    articleExpr.value = values.article_expr ?? inactiveArticleExpr;
    showLegalOptions(preserveDraft ? !articleStyle.hidden : articleNumbering.checked);
    articleError("");
    documentFonts.updateFallback(values);
  },
};

for (const select of stylePanel.querySelectorAll("select[name]")) {
  for (const font of settings.fonts) select.add(new Option(font.label, font.id));
}
styleForm.set({
  list_levels: settings.list_presets?.[settings.new_list_preset],
  ...settings.defaults, ...settings.new_spacing, text_styles: settings.new_text_styles,
  paragraph_layout: settings.new_paragraph_layout,
});
stylePanel.inert = false;

function showReferenceError() {
  errorText.textContent = "참고 서식 검토 화면을 열지 못했습니다. 다시 시도하세요.";
}

function referenceStateChanged() {
  if (nativeDesktop) return;
  if (sectionRules?.length) {
    import("./reference.js").then(module => module.editorChanged()).catch(showReferenceError);
  } else {
    document.dispatchEvent(new Event("reference-editor-change"));
  }
}

byId("open-reference").addEventListener("click", () => {
  import("./reference.js").then(module => module.openReview()).catch(showReferenceError);
});
byId("open-running").addEventListener("click", () => {
  import("./reference.js").then(module => module.openReview(true)).catch(showReferenceError);
});

// Rust settings snapshots can reorder JSON object fields.
function settingsJson(value) {
  return JSON.stringify(value, (_, item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return item;
    return Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]]));
  });
}

function applyConnection(connection = {provider: "rule"}) {
  connectionRevision++;
  if (settingsJson(connection) === settingsJson(decisionConnection)) return;
  decisionConnection = connection;
  document.dispatchEvent(new Event("decision-connection-changed"));
  invalidatePreview();
}

function changed() {
  if (hasActiveArchive()) markDirty("document", true);
  invalidatePreview();
}

function invalidatePreview() {
  version++;
  showAppliedSpacing();
  documentFonts.updateFallback(styleForm.get());
  referenceStateChanged();
  statusText.textContent = proofStatus();
  byId("placeholder").textContent = "미리보기를 갱신하면 조판된 지면이 열립니다.";
}

function download(url, name) {
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
}

function saveBlob(blob, name, onError = showStyleError) {
  if (nativeDesktop) {
    return desktop.save(blob, name).catch(error => {
      onError(error);
      return false;
    });
  }
  const url = URL.createObjectURL(blob);
  try {
    download(url, name);
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return null; // The browser accepts the download but cannot acknowledge a disk write.
}

function bindFilePicker(buttonId, inputId, open) {
  const input = byId(inputId);
  byId(buttonId).addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    const file = input.files[0];
    input.value = "";
    if (file) open(file);
  });
}

function exportPdf() {
  if (nativeDesktop) {
    saveBlob(displayedProof.selected.blob, byId("filename").textContent.replace(/\.(?:md|markdown)$/i, "") + ".pdf");
    return;
  }
  download(preview.src, byId("filename").textContent.replace(/\.(?:md|markdown)$/i, "") + ".pdf");
}

// Pass the decimal string straight to Intl so binary floats never shift a tie.
const ratioFormat = new Intl.NumberFormat("ko-KR", {
  maximumFractionDigits: 2, roundingMode: "halfExpand", useGrouping: false,
});

function appliedPercent(decimal) {
  const value = ratioFormat.format(decimal);
  return (value === "-0" ? "0" : value) + "%";
}

const httpMessages = {
  413: "원고가 요청 크기 한도를 넘었습니다.",
  422: "원고와 출판 양식을 확인하세요.",
  429: "조판 요청이 많습니다. 잠시 후 다시 시도하세요.",
  504: "조판 시간이 초과되었습니다. 다시 시도하세요.",
};

async function fetchProof(style, signal) {
  const connection = decisionConnection.provider === "operator" ? undefined : decisionConnection;
  const source = sourceText();
  if (!nativeDesktop && connection?.api_key && !connectionForm.keyAllowed) throw new Error(byId("decision-key-help").textContent);
  const {files, appliedFonts} = documentFonts.resolveForRender(style);
  if (nativeDesktop) {
    let proof;
    if (files.size) {
      const stored = await desktop.style("export", JSON.stringify(style));
      const frame = documentFonts.frame({source, style: stored, connection: connection ?? {provider: "rule"}}, files);
      proof = await desktop.fontProof(new Uint8Array(await frame.arrayBuffer()), true);
    } else if (!connection || connection.provider === "rule") {
      const result = await desktop.proof(source, style, true);
      proof = result instanceof Blob ? {rule: result} : result;
    } else {
      proof = await desktop.modelProof(source, style, connection, true);
    }
    return {...proof, appliedFonts};
  }
  const request = {source, style, connection};
  const body = files.size ? documentFonts.frame(request, files) : JSON.stringify(request);
  const response = await fetch("/api/render", {
    method: "POST", headers: {
      "Content-Type": files.size ? body.type : "application/json",
      Accept: "multipart/form-data", "X-Jjokgyeol-Spacing": "1",
    },
    signal, body, redirect: "error",
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    const error = new Error(decisionMessage(detail?.code) || detail?.message || httpMessages[response.status] || "조판을 마치지 못했습니다. 다시 시도하세요.");
    error.field = detail?.field;
    throw error;
  }
  const type = response.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase();
  const decision = response.headers.get("X-Jjokgyeol-Decision");
  const reason = response.headers.get("X-Jjokgyeol-Decision-Reason");
  const legacy = connection === undefined;
  const validPdf = file => file instanceof Blob && file.type.split(";")[0] === "application/pdf" && file.size > 0;
  if (type === "application/pdf" && (decision === null || decision === "rule")) {
    const rule = await response.blob();
    if (validPdf(rule)) return {rule, reason, legacy, appliedFonts};
  }
  if (type === "multipart/form-data" && ["rule", "model", "mixed"].includes(decision)) {
    const parts = await response.formData().catch(() => null);
    const rule = parts?.get("rule");
    const model = parts?.get("model");
    if (validPdf(rule) && (decision === "rule" ? model === null : validPdf(model))) {
      let spacing;
      try { spacing = JSON.parse(parts.get("spacing")); } catch { /* Receipts are optional. */ }
      return {rule, model, spacing, decision, reason, legacy, appliedFonts};
    }
  }
  throw new Error("서버에서 올바른 조판 PDF를 받지 못했습니다. 다시 갱신하세요.");
}

// Only current responses acquire URLs. A reference trial transfers these URLs on apply.
class ProofUrls {
  constructor({rule, model, spacing, decision, saved, reason, legacy, appliedFonts}) {
    this.appliedFonts = saved ? saved.appliedFonts : appliedFonts;
    this.reason = decisionMessage(reason);
    this.legacy = legacy;
    const file = (blob, mode, rows, leadingRows) => ({blob, mode, rows, leadingRows, url: URL.createObjectURL(blob)});
    this.rule = rule && file(rule, "rule", spacing?.rule, spacing?.leading?.rule);
    this.model = model && file(model, decision, spacing?.model, spacing?.leading?.model);
    this.saved = saved && file(saved.blob, saved.mode);
  }

  get selected() {
    const mode = this.nativeMode ?? proofChoice.value;
    const file = this.saved || (mode !== "rule" && this.model) || this.rule;
    let label = "규칙 조판";
    if (file.mode === "model") label = "모델 조판";
    else if (file.mode === "mixed") label = "혼합 조판 · 일부 문단에 모델 적용";
    else if (this.legacy && !this.model) label += " · 모델 결과 없음";
    if (this.reason && (!this.model || file === this.model)) label += " · " + this.reason;
    return {...file, href: file.url + "#navpanes=0&view=FitH", label};
  }

  release() {
    for (const file of [this.rule, this.model, this.saved]) {
      if (file) URL.revokeObjectURL(file.url);
    }
  }
}

function spacingCaption(style) {
  const base = appliedPercent(style.ko_tracking_pct);
  const spaces = style.space_pct.map(appliedPercent).join(" ~ ");
  if (style.paragraph_layout !== "justified") {
    return `표시 중인 PDF 적용값 · 한글 자간 ${base} · 어절 공백 ${spaces}`;
  }
  const bounds = style.ko_tracking_bounds_pct;
  const tracking = bounds ? `줄별 최종 자간 허용 범위 ${bounds.map(appliedPercent).join(" ~ ")}` : "줄별 자간 보정 끔";
  return `표시 중인 PDF의 양끝맞춤 설정 · 기본 한글 자간 ${base} · ${tracking} · 어절 공백 ${spaces} 허용`;
}

function displaySelectedProof() {
  if (!displayedProof) return;
  const {href, label} = displayedProof.selected;
  if (!nativeDesktop && preview.src !== href) preview.src = href;
  byId("proof-decision").textContent = "표시 중 · " + label;
  showAppliedSpacing();
}

function editedDocumentOption() {
  documentRevision++;
  markDirty("document", true);
  if (fileLoads.document) finishFileOpen("document", fileLoads.document);
}

let switchingProof = null;
proofChoice.addEventListener("change", async () => {
  if (proofChoice.disabled) return;
  const mode = proofChoice.value;
  if (nativeDesktop) nativePreference = mode;
  if (nativeDesktop && displayedProof?.model) {
    const proof = displayedProof;
    const turn = {proof};
    switchingProof = turn;
    showAppliedSpacing();
    updateFileActions();
    try {
      const pages = await desktop.pages(proof[mode].blob);
      if (turn !== switchingProof || displayedProof !== proof) return;
      byId("desktop-pages").replaceWith(pages);
      proof.nativeMode = mode;
    } catch {
      if (turn !== switchingProof || displayedProof !== proof) return;
      proofChoice.value = nativePreference = proof.nativeMode;
      errorText.textContent = "선택한 교정 PDF를 표시하지 못했습니다.";
      return;
    } finally {
      if (switchingProof === turn) {
        switchingProof = null;
        showAppliedSpacing();
        updateFileActions();
      }
    }
  }
  displaySelectedProof();
  if (includeProof.checked && displayedDocument === documentIdentity && displayedProof?.model) {
    editedDocumentOption();
  }
});

function clearSavedChoice() {
  if (proofChoice.disabled) liveProofChoices(proofChoice.value === "rule" ? "rule" : "model");
}

function proofStatus() {
  if (displayedVersion === version) return displayedProof.completion || "최신 버전";
  if (displayedProof?.saved && displayedDocument === documentIdentity) return "수정 전 교정본 · 갱신 필요";
  return displayedProof ? "이전 버전 · 갱신 필요" : "갱신 필요";
}

function showProof(proof, style, state = "current") {
  clearSavedChoice();
  if (nativeDesktop && !proof.saved) liveProofChoices(nativePreference, Boolean(proof.model || proof.reason));
  if (proof.saved) {
    proof.openedAt = version;
    const {mode, label} = proof.selected;
    proofChoice.replaceChildren(new Option(label.split(" · ")[0], mode));
    proofChoice.disabled = true;
  } else if (includeProof.checked && hasActiveArchive()) {
    documentRevision++;
    markDirty("document", true);
  }
  proof.manuscriptRevision = manuscriptRevision;
  displayedVersion = state === "current" ? version : -1;
  displayedDocument = documentIdentity;
  const previous = displayedProof;
  displayedProof = proof;
  if (switchingProof) {
    switchingProof = null;
    updateFileActions();
  }
  displaySelectedProof();
  previous?.release();
  preview.hidden = nativeDesktop || !inlinePdf;
  byId("viewer-fallback").hidden = nativeDesktop || inlinePdf;
  byId("placeholder").hidden = true;
  byId("proof-size").textContent = style ? `${style.page_mm.join(" × ")} mm · 본문 ${style.body_pt} pt` : "PDF";
  byId("applied-ratios").textContent = style ? spacingCaption(style) : "";
  statusText.textContent = proofStatus();
}

async function render(job) {
  const style = styleForm.get();
  try {
    const files = await fetchProof(style);
    if (pending !== job || version !== job.version) return;
    let mode;
    if (nativeDesktop) {
      let pages;
      const desiredMode = () => files.model ? (job.ai ? "model" : nativePreference) : "rule";
      do {
        mode = desiredMode();
        pages = await desktop.pages(files[mode]);
        if (pending !== job || version !== job.version) return;
      } while (mode !== desiredMode());
      byId("desktop-pages")?.remove();
      preview.parentNode.append(pages);
    }
    const proof = new ProofUrls(files);
    proof.nativeMode = mode;
    if (job.ai || job.attemptsModel) {
      const completion = {model: "AI 조판 완료", mixed: "일부 AI 적용", rule: "규칙 조판 완료"};
      proof.completion = completion[proof.model?.mode ?? "rule"] + (proof.reason ? " · " + proof.reason : "");
    }
    if (job.ai && proof.model) {
      nativePreference = "model";
      proofChoice.value = "model";
    }
    showProof(proof, style);
    if (job.exporting) exportPdf();
  } catch (error) {
    if (pending !== job || version !== job.version) return;
    const field = error.field;
    const message = error instanceof TypeError ? "조판에 실패했습니다. 연결을 확인하고 다시 시도하세요." : error.message;
    showStyleFieldError(field, message, {clearMargin: true});
    errorText.textContent = field ? `${field} · ${message}` : message;
    statusText.textContent = displayedVersion === version ? "현재 지면 · 갱신 실패" :
      displayedProof ? "이전 버전 · 조판 실패" : "조판 실패";
    byId("placeholder").textContent = "원고와 양식을 확인한 뒤 미리보기를 갱신하세요.";
  } finally {
    if (pending === job) { pending = null; showAppliedSpacing(); }
  }
}

function aiProgress() {
  return displayedProof ? "AI 조판 중 · 이전 지면 유지" : "AI 조판 중";
}

async function requestPdf(exporting, ai = false) {
  if (!paragraphDraftValid() || (pendingAlignmentEdit && !await pendingAlignmentEdit)) return;
  if (fileLoads.source || fileLoads.style) return;
  if (pending?.version === version) {
    pending.exporting ||= exporting;
    if (ai && !pending.ai) {
      pending.ai = true;
      statusText.textContent = aiProgress();
    }
    return;
  }
  if (exporting && (displayedVersion === version || displayedProof?.openedAt === version)) {
    exportPdf();
    return;
  }
  const attemptsModel = decisionConnection.provider !== "rule" &&
    (!nativeDesktop || decisionConnection.provider !== "operator");
  pending = {version, exporting, ai, attemptsModel};
  showAppliedSpacing();
  errorText.textContent = "";
  marginError();
  statusText.textContent = ai || attemptsModel
    ? aiProgress()
    : (displayedProof && displayedVersion !== version ? "이전 버전 · 조판 중" : "조판 중");
  if (!displayedProof) byId("placeholder").textContent = "첫 지면을 조판하고 있습니다.";
  render(pending);
}

function updateFileActions() {
  const busy = Boolean(fileLoads.source || fileLoads.style);
  const choosing = switchingProof !== null && switchingProof.proof === displayedProof;
  byId("save-document").disabled = busy || savingDocument !== null || choosing;
  byId("refresh").disabled = busy;
  byId("export").disabled = busy || choosing;
  byId("save").disabled = Boolean(fileLoads.source);
  pinButton.disabled = Boolean(fileLoads.source);
  scheduleAlignmentQuery();
  byId("save-style").disabled = Boolean(fileLoads.style);
  byId("save-personal-defaults").disabled = Boolean(fileLoads.style) || stylePanel.inert;
  if (busy) statusText.textContent = fileLoads.document ? "쪽결 문서 불러오는 중" :
    fileLoads.style ? "양식 불러오는 중" : "원고 처리 중";
}

// A document read owns both slots. Replacing either half cancels the entire read.
function cancelFileOpen(kind) {
  if (kind === "document" || fileLoads.document) {
    fileLoads.source = fileLoads.style = null;
  } else {
    fileLoads[kind] = null;
  }
}

function beginFileOpen(kind, token = Symbol()) {
  documentFonts.cancel();
  // Opening a file replaces startup defaults. Typing in the same manuscript keeps its style.
  if (fileLoads.style === startupStyleToken) fileLoads.style = null;
  cancelFileOpen(kind);
  if (kind !== "source") document.dispatchEvent(new Event("style-suggestion-invalidate"));
  if (kind === "document") fileLoads.source = fileLoads.style = token;
  else fileLoads[kind] = token;
  referenceStateChanged();
  pending = null;
  errorText.textContent = "";
  if (kind !== "source") settingsError.textContent = "";
  updateFileActions();
  return token;
}

function finishFileOpen(kind, token) {
  if (fileLoads[kind] !== token) return;
  cancelFileOpen(kind);
  referenceStateChanged();
  updateFileActions();
  if (fileLoads.source || fileLoads.style) return;
  statusText.textContent = proofStatus();
  if (initialPreviewNeeded) {
    initialPreviewNeeded = false;
    if (!errorText.textContent) requestPdf(false);
  }
}

async function openFile(kind, read, apply, onError, confirmReplacement) {
  const turn = beginFileOpen(kind);
  const current = () => fileLoads[kind] === turn;
  try {
    if (confirmReplacement && (!await confirmReplacement(current) || !current())) return;
    const data = await read();
    if (current()) {
      apply(data);
      changed();
    }
  } catch (error) {
    if (current()) onError(error);
  } finally {
    finishFileOpen(kind, turn);
  }
}

async function readMarkdown(file) {
  const input = file || await fetch(nativeDesktop ? "sample.md" : "/sample.md", {redirect: "error"});
  if (!file && !input.ok) throw new Error();
  return new TextDecoder("utf-8", {fatal: true, ignoreBOM: true}).decode(await input.arrayBuffer());
}

function applyManuscript(text, name, warnings = []) {
  documentIdentity++;
  documentFilename = null;
  includeProof.checked = true;
  markDirty("document", false);
  withBom = text.startsWith("\uFEFF");
  const manuscript = withBom ? text.slice(1) : text;
  alignmentUndo.length = alignmentRedo.length = 0;
  source.value = manuscript;
  rememberSource(manuscript);
  manuscriptRevision++;
  markDirty("source", false);
  byId("filename").textContent = name;
  const list = byId("import-warnings");
  list.replaceChildren();
  for (const warning of warnings) {
    const item = document.createElement("li");
    item.textContent = warning;
    list.append(item);
  }
  byId("import-notice").hidden = !warnings.length;
}

async function openMarkdown(file) {
  initialPreviewNeeded = false;
  return openFile("source", () => readMarkdown(file), text => {
    applyManuscript(text, file?.name || "비교 원고.md");
    initialPreviewNeeded = !file;
  }, () => {
    errorText.textContent = file ? "원고를 열지 못했습니다. UTF-8 파일인지 확인하세요." :
      "샘플을 불러오지 못했습니다. 원고를 열거나 직접 입력하세요.";
    if (!displayedProof) {
      statusText.textContent = "원고 열기 실패";
      byId("placeholder").textContent = "원고를 입력하고 미리보기를 갱신하세요.";
    }
  }, confirmManuscriptReplacement);
}

async function requestImport(file) {
  const query = new URLSearchParams({name: file.name});
  const response = await fetch("/api/import?" + query, {
    method: "POST", body: file, redirect: "error", headers: {"Content-Type": "application/octet-stream"},
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(data?.message || "원고를 가져오지 못했습니다. 다시 시도하세요.");
  if (typeof data?.markdown !== "string" || !Array.isArray(data.warnings) ||
      !data.warnings.every(warning => typeof warning === "string")) {
    throw new Error("원고 가져오기 결과를 읽을 수 없습니다. 다시 시도하세요.");
  }
  return data;
}

async function importManuscript(file) {
  initialPreviewNeeded = false;
  return openFile("source", () => requestImport(file), ({markdown, warnings}) => {
    applyManuscript(markdown, file.name.replace(/\.[^.]*$/, "") + ".md", warnings);
  }, error => {
    errorText.textContent = error instanceof TypeError ? "연결을 확인하고 원고 가져오기를 다시 시도하세요." : error.message;
  }, confirmManuscriptReplacement);
}

function edited(kind) {
  if (kind === "source") manuscriptRevision++;
  else styleRevision++;
  markDirty(kind, true);
  if (kind === "style") {
    typography.clearError();
    document.dispatchEvent(new Event("style-suggestion-invalidate"));
  }
  cancelFileOpen(kind);
  initialPreviewNeeded = false;
  errorText.textContent = "";
  if (kind === "style") settingsError.textContent = "";
  changed();
  updateFileActions();
}

function selectedLines(start, end) {
  const lineAt = index => source.value.slice(0, index).split("\n").length - 1;
  return {first_line: lineAt(start), last_line: lineAt(Math.max(start, end - 1))};
}

async function requestPin() {
  const payload = {source: sourceText(), ...selectedLines(source.selectionStart, source.selectionEnd)};
  const roles = Object.entries(typography.get().text_styles ?? {}).filter(([, fields]) =>
    fields.align || fields.space_before_pt != null || fields.space_after_pt != null);
  if (roles.length) payload.aligned_roles = roles.map(([role]) => role);
  const response = await fetch("/api/article/pin", {
    method: "POST", redirect: "error", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok || typeof data?.source !== "string") {
    throw new Error(data?.message || httpMessages[response.status] || "번호 고정 상태를 변경하지 못했습니다.");
  }
  return data.source;
}

function applyPinnedSource(text) {
  const after = text.replace(/\r\n?/g, "\n");
  // Replace only the edited range to keep the selection and scroll position.
  const {start, end, nextEnd} = editedRange(source.value, after);
  source.setRangeText(after.slice(start, nextEnd), start, end, "preserve");
  rememberSource(text);
  manuscriptRevision++;
  markDirty("source", true);
  if (document.activeElement === document.body || document.activeElement === pinButton) source.focus({preventScroll: true});
}

const paragraphAlign = byId("paragraph-align");
paragraphAlign.add(new Option("양식 따르기", ""));
for (const [label, value] of alignmentChoices) paragraphAlign.add(new Option(label, value));
const paragraphDock = byId("paragraph-dock");
const paragraphDockToggle = byId("toggle-paragraph-dock");
function setParagraphDock(open) {
  paragraphDock.hidden = !open;
  paragraphDockToggle.setAttribute("aria-expanded", String(open));
}
paragraphDockToggle.addEventListener("click", () => setParagraphDock(paragraphDock.hidden));
byId("close-paragraph-dock").addEventListener("click", () => {
  setParagraphDock(false);
  paragraphDockToggle.focus();
});

function toggleEmphasis(marker) {
  const {selectionStart: start, selectionEnd: end, selectionDirection: direction} = source;
  source.focus({preventScroll: true});
  if (start === end) return;
  const value = source.value;
  const left = value.slice(Math.max(0, start - 4), start).match(/\*+$/)?.[0].length;
  const right = value.slice(end, end + 4).match(/^\*+/)?.[0].length;
  const unwrap = left === right && (left === marker.length || left === 3);
  const raw = sourceText();
  const [rawStart, rawEnd] = rawSourceRange(raw, start, end);
  const selected = raw.slice(rawStart, rawEnd);
  const remove = unwrap ? marker.length : 0;
  const replacement = unwrap ? selected : marker + selected + marker;
  try {
    const formatted = raw.slice(0, rawStart - remove) + replacement + raw.slice(rawEnd + remove);
    applyFormattedSource(formatted, {start, end, direction});
    const innerStart = start + (unwrap ? -marker.length : marker.length);
    source.setSelectionRange(innerStart, innerStart + end - start, direction);
  } catch (error) {
    errorText.textContent = error.message;
  }
}
byId("format-bold").addEventListener("click", () => toggleEmphasis("**"));
byId("format-italic").addEventListener("click", () => toggleEmphasis("*"));

const menus = [...document.querySelectorAll(".app-menu")];
for (const menu of menus) {
  const summary = menu.querySelector("summary");
  summary.setAttribute("aria-haspopup", "menu");
  summary.setAttribute("aria-expanded", "false");
  menu.addEventListener("toggle", () => {
    summary.setAttribute("aria-expanded", String(menu.open));
    if (menu.open) menus.filter(other => other !== menu).forEach(other => { other.open = false; });
  });
  const items = () => [...menu.querySelectorAll('[role="menuitem"]')]
    .filter(item => item.getClientRects().length > 0);
  menu.querySelector("summary").addEventListener("keydown", event => {
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    event.preventDefault();
    menu.open = true;
    const visible = items();
    visible[event.key === "ArrowDown" ? 0 : visible.length - 1]?.focus();
  });
  menu.querySelector("[role=menu]").addEventListener("keydown", event => {
    const visible = items();
    const index = visible.indexOf(document.activeElement);
    const next = event.key === "ArrowDown" ? (index + 1) % visible.length :
      event.key === "ArrowUp" ? (index - 1 + visible.length) % visible.length :
      event.key === "Home" ? 0 : event.key === "End" ? visible.length - 1 : -1;
    if (next < 0) return;
    event.preventDefault();
    visible[next]?.focus();
  });
}
document.addEventListener("click", event => {
  if (!event.target.closest(".app-menu")) menus.forEach(menu => { menu.open = false; });
});
document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  const menu = menus.find(item => item.open);
  if (!menu) return;
  event.preventDefault();
  menu.open = false;
  menu.querySelector("summary").focus();
});
for (const item of document.querySelectorAll(".menu-items [data-command]")) {
  item.addEventListener("click", () => {
    const command = item.dataset.command;
    if (command === "undo" || command === "redo") {
      source.focus({preventScroll: true});
      document.execCommand(command);
    } else if (command === "paragraph-align") {
      paragraphAlign.focus({preventScroll: true});
    } else {
      byId(command)?.click();
    }
    menus.forEach(menu => { menu.open = false; });
  });
}
const paragraphSpacing = byId("paragraph-spacing");
const individualSpacing = paragraphSpacingFields(paragraphSpacing, "양식 따르기");
const paragraphLeading = byId("paragraph-leading");
const leadingMode = byId("paragraph-leading-mode");
const leadingValue = byId("paragraph-leading-value");
const leadingMeasure = byId("paragraph-leading-measure");
for (const [label, value] of [["양식 따르기", ""], ["AI 자동", "auto"], ["직접 입력", "manual"]]) {
  leadingMode.add(new Option(label, value));
}
const spacingSources = {manual: "직접", style: "양식", ai: "AI", rule: "규칙 대체", default: "기본"};
const spPerPt = settings.indent_units?.sp_per_pt;
paragraphSpacing.hidden = !(spPerPt && settings.paragraph_spacing);
paragraphLeading.hidden = !(spPerPt && settings.leading_range_pt);
let selectedParagraphLine = null;

function exactPtFromSp(sp) {
  const value = BigInt(sp), whole = value / BigInt(spPerPt), rest = value % BigInt(spPerPt);
  const fraction = (rest * 152587890625n).toString().padStart(16, "0").replace(/0+$/, "");
  return fraction ? `${whole}.${fraction}` : String(whole);
}

function showAppliedSpacing() {
  const caption = byId("paragraph-spacing-applied");
  const selected = displayedProof?.selected;
  const rows = selected?.rows;
  const leadingRows = selected?.leadingRows;
  const proof = displayedProof;
  const target = alignmentTarget;
  const line = selectedParagraphLine;
  const currentMeasurement = () => proof === displayedProof && selected?.url === proof?.selected.url &&
    line === selectedParagraphLine && displayedVersion === version && !pending && !switchingProof && !fileLoads.style;
  const pinAvailable = currentMeasurement() && currentTarget(target);
  const pinValue = (field, sp) => {
    const pin = document.createElement("button");
    pin.type = "button";
    pin.textContent = "고정";
    pin.disabled = Boolean(pendingAlignmentEdit);
    pin.addEventListener("click", () => {
      if (target === alignmentTarget && currentTarget(target) && currentMeasurement()) {
        editParagraph({[field]: exactPtFromSp(sp)}, currentMeasurement);
      }
    });
    return pin;
  };
  if ((!Array.isArray(rows) && !Array.isArray(leadingRows)) || displayedDocument !== documentIdentity ||
      displayedProof.manuscriptRevision !== manuscriptRevision || pending || switchingProof || fileLoads.source || fileLoads.style ||
      !Number.isInteger(selectedParagraphLine)) {
    caption.textContent = displayedProof ? "선택 문단 여백 · 미리보기 갱신 후 확인" : "";
    return;
  }
  const boundary = (label, lineKey, field) => {
    const row = rows?.find(record => record?.[lineKey] === selectedParagraphLine);
    if (!row) return label + " 측정 안 됨";
    if (row.page_break === true) return label + " 경계 없음";
    if (!Number.isSafeInteger(row.gap_sp) || row.gap_sp < 0 || !Object.hasOwn(spacingSources, row.source)) {
      return label + " 확인 불가";
    }
    const text = document.createElement("span");
    text.textContent = label + " " + row.gap_sp / spPerPt + " pt · " + spacingSources[row.source];
    if (row.source === "ai" && pinAvailable) {
      text.append(" ", pinValue(field, row.gap_sp));
    }
    return text;
  };
  caption.replaceChildren();
  caption.append(displayedVersion === version ? "선택 문단 적용값 · " : "이전 교정본의 선택 문단 적용값 · ", boundary("위", "after_line", "space_before_pt"), " / ", boundary("아래", "before_line", "space_after_pt"));
  const leading = leadingRows?.find(record => record?.line === selectedParagraphLine);
  if (leading) {
    const value = leading.variable ? "줄별로 다름" : Number.isSafeInteger(leading.leading_sp) && leading.leading_sp > 0
      ? `${leading.leading_sp / spPerPt} pt · ${spacingSources[leading.source]}` : "확인 불가";
    caption.append(" / 줄 간격 ", value);
    if (!leading.variable && leading.source === "ai" && Number.isSafeInteger(leading.leading_sp) && leading.leading_sp > 0 && pinAvailable) {
      caption.append(" ", pinValue("leading_pt", leading.leading_sp));
    }
  } else {
    caption.append(" / 줄 간격 측정 안 됨");
  }
}

const alignmentMessage = byId("paragraph-align-message");
let alignmentTimer;
let alignmentTarget;
let lastAlignmentQuery;
let alignmentQueue = Promise.resolve();
let pendingAlignmentEdit = null;
let pendingSpacingEdit = null;

function queueAlignment(target, patch, currentMeasurement) {
  // One worker per editor. Stale cursor lookups skip their turn before reading the manuscript.
  alignmentQueue = alignmentQueue.then(() => updateAlignment(target, patch, currentMeasurement));
  return alignmentQueue;
}


function currentTarget(target) {
  return target && !fileLoads.source && target.revision === manuscriptRevision &&
    target.start === source.selectionStart && target.end === source.selectionEnd &&
    target.direction === source.selectionDirection;
}

function alignmentSelection() {
  return {revision: manuscriptRevision, start: source.selectionStart, end: source.selectionEnd,
    direction: source.selectionDirection};
}

async function requestAlignment(payload) {
  if (nativeDesktop) return desktop.paragraphAlign(payload);
  const response = await fetch("/api/paragraph/align", {
    method: "POST", redirect: "error", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok || typeof data?.source !== "string") {
    throw new Error(data?.message || httpMessages[response.status] || "문단 정렬을 확인하지 못했습니다.");
  }
  return data;
}

function closeWebKitUndoGroup() {
  if (navigator.vendor !== "Apple Computer, Inc." || !document.queryCommandEnabled("undo")) return;
  // WebKit groups native edits across script calls. Cycle its undo group while cancelling
  // both beforeinput events so no text, selection or application state is replayed.
  const cancelHistory = event => {
    if (event.inputType === "historyUndo" || event.inputType === "historyRedo") {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  };
  window.addEventListener("beforeinput", cancelHistory, true);
  try {
    if (document.execCommand("undo")) document.execCommand("redo");
  } finally {
    window.removeEventListener("beforeinput", cancelHistory, true);
  }
}

function applyFormattedSource(text, selection) {
  const before = source.value;
  const rawBefore = sourceText();
  const after = text.replace(/\r\n?/g, "\n");
  if (before !== after) {
    // Anchor the difference at a line boundary, even when the target starts with "<".
    const prefix = editedRange(before, after).start;
    const lineStart = before.slice(0, prefix).lastIndexOf("\n") + 1;
    const {start, end, nextEnd} = editedRange(before, after, lineStart);
    const offset = index => index < start ? index : index >= end ? index + nextEnd - end : Math.min(index, nextEnd);
    source.focus({preventScroll: true});
    closeWebKitUndoGroup();
    source.setSelectionRange(start, end);
    const replacement = after.slice(start, nextEnd);
    // Native editing commands include this replacement in the textarea's undo history.
    if (!document.execCommand(replacement ? "insertText" : "delete", false, replacement)) {
      source.setSelectionRange(selection.start, selection.end, selection.direction);
      throw new Error("원고 서식을 적용하지 못했습니다.");
    }
    closeWebKitUndoGroup();
    if (mixedEndings) {
      const raw = editedRange(rawBefore, text);
      alignmentUndo.push({start: raw.start, before: rawBefore.slice(raw.start, raw.end), after: text.slice(raw.start, raw.nextEnd)});
    }
    rememberSource(text);
    source.setSelectionRange(offset(selection.start), offset(selection.end), selection.direction);
  } else {
    source.focus({preventScroll: true});
  }
}

async function updateAlignment(target, patch, currentMeasurement) {
  const measurementChanged = () => currentMeasurement && !currentMeasurement();
  const resetTarget = () => {
    alignmentTarget = null;
    scheduleAlignmentQuery();
  };
  if (target !== alignmentTarget || !currentTarget(target)) return;
  if (measurementChanged()) return resetTarget();
  const lines = selectedLines(target.start, target.end);
  const query = patch === undefined;
  const sameLines = lastAlignmentQuery?.revision === target.revision &&
    lastAlignmentQuery.first_line === lines.first_line && lastAlignmentQuery.last_line === lines.last_line;
  const options = query ? (paragraphSpacing.hidden ? {} : {spacing: true, leading: true}) :
    (Object.hasOwn(patch, "space_before_pt") || Object.hasOwn(patch, "space_after_pt") ? {...patch, leading: true} : patch);
  try {
    const data = query && sameLines ? lastAlignmentQuery.result :
      await requestAlignment({source: sourceText(), ...lines, ...options});
    if (target !== alignmentTarget || !currentTarget(target)) return;
    if (measurementChanged()) return resetTarget();
    const focus = document.activeElement;
    const output = ["save", "save-document", "refresh", "export"].includes(focus?.id);
    const outside = ![source, paragraphAlign, document.body].includes(focus) &&
      !paragraphSpacing.contains(focus) && !paragraphLeading.contains(focus) && !byId("source-toolbar").contains(focus);
    if (!query && Object.hasOwn(patch, "align") && outside && !output) {
      return resetTarget();
    }
    if (!query && data.supported) {
      applyFormattedSource(data.source, target);
      if (outside) focus.focus({preventScroll: true});
    }
    paragraphAlign.disabled = false;
    paragraphAlign.value = data.align ?? "";
    alignmentMessage.textContent = data.message;
    if (!query && !Object.hasOwn(data, "start_line")) {
      // Alignment-only replies use the legacy shape. Look up spacing at the restored selection.
      resetTarget();
    } else {
      clearTimeout(alignmentTimer);
      alignmentTarget = alignmentSelection();
      selectedParagraphLine = data.start_line;
      paragraphSpacing.disabled = !data.supported || !Number.isInteger(data.start_line);
      paragraphLeading.disabled = paragraphSpacing.disabled;
      for (const field of individualSpacing) field.set(data[field.key]);
      const leading = data.leading_pt;
      leadingMode.value = leading === "auto" ? "auto" : leading == null ? "" : "manual";
      leadingValue.value = leadingMode.value === "manual" ? String(leading) : "";
      leadingMeasure.hidden = leadingMode.value !== "manual";
      byId("paragraph-target-help").textContent = Number.isInteger(data.start_line)
        ? `선택 문단 · 원고 ${data.start_line + 1}행${target.start === target.end ? " · 현재 줄" : " · 선택 범위"}` : "현재 위치에서 편집할 문단을 찾을 수 없습니다.";
      const {source: updatedSource, ...result} = data;
      lastAlignmentQuery = {...selectedLines(alignmentTarget.start, alignmentTarget.end), revision: manuscriptRevision,
        result};
      showAppliedSpacing();
    }
    return data.supported;
  } catch (error) {
    if (target !== alignmentTarget || !currentTarget(target)) return;
    if (measurementChanged()) return resetTarget();
    paragraphAlign.selectedIndex = -1;
    paragraphAlign.disabled = false;
    paragraphSpacing.disabled = !Number.isInteger(selectedParagraphLine);
    paragraphLeading.disabled = paragraphSpacing.disabled;
    alignmentMessage.textContent = error.message || "문단 설정을 확인하지 못했습니다.";
    alignmentTarget = null; // Retry a failed lookup on the next interaction.
  }
}

function scheduleAlignmentQuery() {
  if (settingsView) return;
  if (currentTarget(alignmentTarget)) return;
  paragraphAlign.disabled = Boolean(fileLoads.source);
  clearTimeout(alignmentTimer);
  paragraphAlign.selectedIndex = -1;
  paragraphSpacing.disabled = true;
  paragraphLeading.disabled = true;
  for (const field of individualSpacing) field.set(null);
  selectedParagraphLine = null;
  showAppliedSpacing();
  alignmentMessage.textContent = "";
  if (paragraphAlign.disabled) {
    alignmentTarget = null;
    return;
  }
  const target = alignmentTarget = alignmentSelection();
  alignmentTimer = setTimeout(() => queueAlignment(target), 250);
}

function editParagraph(patch, currentMeasurement) {
  if (pendingAlignmentEdit) return;
  clearTimeout(alignmentTimer);
  const target = alignmentTarget = currentMeasurement ? alignmentTarget : alignmentSelection();
  paragraphAlign.disabled = paragraphSpacing.disabled = paragraphLeading.disabled = true;
  const edit = pendingAlignmentEdit = queueAlignment(target, patch, currentMeasurement);
  showAppliedSpacing();
  if (!Object.hasOwn(patch, "align")) pendingSpacingEdit = edit;
  edit.finally(() => {
    if (pendingSpacingEdit === edit) pendingSpacingEdit = null;
    if (pendingAlignmentEdit === edit) {
      pendingAlignmentEdit = null;
      showAppliedSpacing();
    }
  });
}
paragraphAlign.addEventListener("change", () => editParagraph({align: paragraphAlign.value || null}));

function validParagraphAmount(field) {
  const [min, max] = settings.paragraph_spacing.range_pt;
  const raw = field.input.value;
  if (compareDecimal(raw, min) >= 0 && compareDecimal(raw, max) <= 0) return true;
  field.input.setAttribute("aria-invalid", "true");
  alignmentMessage.textContent = `문단 여백은 ${min}~${max} pt 범위의 수로 입력하세요.`;
  return false;
}

function paragraphAmount(raw) {
  const pt = Number(raw);
  const scaled = pt * spPerPt;
  const lower = Math.floor(scaled);
  if (scaled - lower !== 0.5) return pt;
  // A half-sp has at most 17 decimal places. JSON's shortest float can cross that boundary.
  const roundUp = compareDecimal(raw, pt.toFixed(17)) >= 0;
  return (lower + Number(roundUp)) / spPerPt;
}

function paragraphDraftValid() {
  if (!paragraphSpacing.disabled && !individualSpacing.every(field => field.mode.value !== "manual" || validParagraphAmount(field))) return false;
  if (!paragraphLeading.disabled && leadingMode.value === "manual") {
    const [min, max] = settings.leading_range_pt;
    if (compareDecimal(leadingValue.value, min) >= 0 && compareDecimal(leadingValue.value, max) <= 0) return true;
    leadingValue.setAttribute("aria-invalid", "true");
    alignmentMessage.textContent = `줄 간격은 ${min}~${max} pt 범위의 수로 입력하세요.`;
    return false;
  }
  return true;
}

leadingMode.addEventListener("change", () => {
  alignmentMessage.textContent = "";
  leadingMeasure.hidden = leadingMode.value !== "manual";
  if (leadingMode.value === "manual") leadingValue.focus();
  else editParagraph({leading_pt: leadingMode.value === "auto" ? "auto" : null});
});
leadingValue.addEventListener("input", () => {
  leadingValue.removeAttribute("aria-invalid"); alignmentMessage.textContent = "";
});
leadingValue.addEventListener("change", () => {
  if (leadingMode.value !== "manual") return;
  const [min, max] = settings.leading_range_pt;
  if (compareDecimal(leadingValue.value, min) >= 0 && compareDecimal(leadingValue.value, max) <= 0) {
    editParagraph({leading_pt: leadingValue.value});
  } else {
    leadingValue.setAttribute("aria-invalid", "true");
    alignmentMessage.textContent = `줄 간격은 ${min}~${max} pt 범위의 수로 입력하세요.`;
  }
});
leadingValue.addEventListener("keydown", event => {
  if (event.key === "Enter") { event.preventDefault(); leadingValue.blur(); }
});

for (const field of individualSpacing) {
  field.input.setAttribute("aria-describedby", alignmentMessage.id);
  field.mode.addEventListener("change", () => {
    alignmentMessage.textContent = "";
    if (field.mode.value === "manual") field.input.focus();
    else editParagraph({[field.key]: field.mode.value || null});
  });
  field.input.addEventListener("input", () => {
    field.input.removeAttribute("aria-invalid");
    alignmentMessage.textContent = "";
  });
  field.input.addEventListener("change", () => {
    if (field.mode.value === "manual" && validParagraphAmount(field)) editParagraph({[field.key]: paragraphAmount(field.input.value)});
  });
  field.input.addEventListener("keydown", event => {
    if (event.key === "Enter") {
      event.preventDefault();
      if (validParagraphAmount(field)) field.input.blur();
    }
  });
}
paragraphAlign.addEventListener("focus", scheduleAlignmentQuery);
source.addEventListener("select", scheduleAlignmentQuery);
source.addEventListener("selectionchange", scheduleAlignmentQuery);
document.addEventListener("selectionchange", () => {
  if (document.activeElement === source) scheduleAlignmentQuery();
});

source.addEventListener("beforeinput", () => { editStart = source.selectionStart; });
source.addEventListener("input", editManuscript);
pinButton.addEventListener("click", () => openFile("source", requestPin, applyPinnedSource, error => {
  errorText.textContent = error instanceof TypeError ? "연결을 확인하고 번호 고정을 다시 시도하세요." : error.message;
}));
articleNumbering.addEventListener("input", () => {
  articleExpr.disabled = !articleNumbering.checked;
  articleError("");
});
articleExpr.addEventListener("input", () => articleError(""));
for (const field of [pageWidth, pageHeight]) field.addEventListener("input", syncPagePreset);
pagePreset.addEventListener("input", event => {
  if (pagePreset.value === "custom") {
    event.stopPropagation();
    pageWidth.focus();
    return;
  }
  [pageWidth.value, pageHeight.value] = pagePresets[pagePreset.value];
});
marginMode.addEventListener("input", event => {
  event.stopPropagation();
  const onError = error => {
    marginError(error.field, error.message);
    showStyleError(error);
  };
  const geometry = marginGeometry(styleForm.get());
  if (marginMode.value === "mirrored") {
    if (!matchesSplit()) {
      setMarginMode(true, "0");
      edited("style");
      return;
    }
    const {fixed, gutter_sp} = marginSplit;
    const draft = restoredMarginDraft(geometry, marginSplit);
    const widthUnchanged = sameDecimal(geometry.page_mm[0], fixed.page_mm[0]) &&
      indentKeys.every(key => sameDecimal(geometry[key], fixed[key])) &&
      JSON.stringify(geometry.section_rules) === JSON.stringify(fixed.section_rules);
    if (widthUnchanged || gutter_sp === 0 && decimalKey(draft.gutter_mm) === "0") {
      applyMarginGeometry(draft);
      edited("style");
    } else {
      marginMode.value = "fixed";
      openFile("style", () => validatedMarginRestore(draft, geometry, gutter_sp), values => {
        applyMarginGeometry(values);
        styleRevision++;
        markDirty("style", true);
      }, onError);
    }
  } else if (!gutter.disabled) {
    marginMode.value = "mirrored";
    const apply = ({fixed, gutter_sp}) => {
      marginSplit = {raw: geometry, fixed, gutter_sp};
      applyMarginGeometry(fixed);
    };
    if (decimalKey(geometry.gutter_mm) === "0") {
      apply({fixed: {...geometry, gutter_mm: undefined}, gutter_sp: 0});
      edited("style");
      return;
    }
    openFile("style", () => fixedMargins(geometry), values => {
      apply(values);
      styleRevision++;
      markDirty("style", true);
    }, onError);
  }
});
for (const control of [margins[1], margins[3]]) {
  control.addEventListener("change", () => {
    if (gutter.disabled && !matchesSplit()) marginSplit = undefined;
  });
}
stylePanel.addEventListener("input", () => {
  updateMarginSummary();
  marginError();
  edited("style");
});
bindFilePicker("open", "file", openMarkdown);
bindFilePicker("import-source", "import-file", importManuscript);
byId("refresh").addEventListener("click", () => requestPdf(false));
byId("export").addEventListener("click", () => requestPdf(true));
byId("ai-typeset").addEventListener("click", () => requestPdf(false, true));
byId("save").addEventListener("click", async () => {
  if (!paragraphDraftValid() || (pendingAlignmentEdit && !await pendingAlignmentEdit)) return;
  const revision = manuscriptRevision;
  const blob = new Blob([manuscriptFileText()], {type: "text/markdown;charset=utf-8"});
  if (await saveBlob(blob, byId("filename").textContent) && revision === manuscriptRevision) {
    markDirty("source", false);
  }
});
const settingsSnapshot = () => ({
  style: styleForm.get(), dirty: styleDirty, inactiveArticleExpr: articleExpr.value,
  baseRevision: styleRevision,
});

const startupDefaults = initializePersonalDefaults();

if (settingsView) {
  settingsDialog.showModal();
  const applyButton = byId("apply-settings");
  const closeButton = byId("close-settings");
  byId("decision-settings").disabled = true;
  stylePanel.inert = true;
  applyButton.disabled = true;
  byId("save-personal-defaults").disabled = true;
  let initialSnapshot;
  desktop.invoke("settings_snapshot").then(snapshot => {
    initialSnapshot = snapshot;
    documentFonts.receiveChoices(snapshot.fontChoices);
    byId("personal-defaults-status").textContent = snapshot.defaultsMessage ?? "";
    openSettingsConnection(snapshot.connection);
    byId("decision-settings").disabled = false;
    styleForm.set(snapshot.style, {inactiveArticleExpr: snapshot.inactiveArticleExpr});
    styleDirty = snapshot.dirty;
    stylePanel.inert = false;
    applyButton.disabled = false;
    byId("save-personal-defaults").disabled = false;
  }).catch(error => showStyleError(error));
  const cancel = () => desktop.invoke("cancel_settings").catch(error => showStyleError(error));
  closeButton.addEventListener("click", cancel);
  settingsDialog.addEventListener("cancel", event => { event.preventDefault(); if (!closeButton.disabled) cancel(); });
  applyButton.addEventListener("click", async () => {
    if (fileLoads.style || styleSavesPending || !connectionForm.valid()) return;
    const revision = styleRevision;
    const snapshot = {...settingsSnapshot(), baseRevision: initialSnapshot.baseRevision};
    byId("decision-settings").disabled = true;
    applyButton.disabled = true;
    closeButton.disabled = true;
    stylePanel.inert = true;
    settingsError.textContent = "";
    try {
      const stored = await requestStyle("export", JSON.stringify(snapshot.style));
      await requestStyle("open", JSON.stringify(stored));
      if (revision !== styleRevision || fileLoads.style) return;
      await desktop.invoke("apply_settings", {snapshot: {...snapshot, connection: connectionForm.value()}});
    } catch (error) {
      if (revision === styleRevision) {
        showStyleFieldError(error.field, error.message, {clearMargin: true});
        showStyleError(error);
      }
    } finally {
      byId("decision-settings").disabled = false;
      stylePanel.inert = false;
      closeButton.disabled = false;
      applyButton.disabled = false;
    }
  });
} else if (nativeDesktop) {
  let latestRevision = 0;
  const openSettings = byId("open-settings");
  const listener = window.__TAURI__.event?.listen?.("settings-applied", async event => {
    const snapshot = event.payload;
    const revision = snapshot.connectionRevision;
    let connection;
    if (revision !== undefined) {
      if (revision <= latestRevision) return;
      latestRevision = revision;
      openSettings.disabled = true;
      try {
        connection = await desktop.invoke("take_settings_connection", {revision}) ?? undefined;
      } catch {
        if (revision === latestRevision) errorText.textContent = "연결 설정을 적용하지 못했습니다. 다시 열어 확인하세요.";
        return;
      } finally {
        if (revision === latestRevision) openSettings.disabled = false;
      }
      if (revision !== latestRevision) return;
    }
    if (snapshot.baseRevision !== styleRevision) {
      errorText.textContent = "설정창을 연 뒤 양식이 바뀌어 적용하지 못했습니다. 다시 열어 확인하세요.";
      return;
    }
    const changedStyle = settingsJson(styleForm.get()) !== settingsJson(snapshot.style);
    if (changedStyle || snapshot.inactiveArticleExpr !== articleExpr.value) {
      styleForm.set(snapshot.style, {preserveDraft: true, inactiveArticleExpr: snapshot.inactiveArticleExpr});
    }
    if (changedStyle) {
      styleRevision++;
      cancelFileOpen("style");
      updateFileActions();
      changed();
    }
    markDirty("style", snapshot.dirty);
    if (revision !== undefined) applyConnection(connection);
  }) ?? Promise.resolve();
  openSettings.addEventListener("click", async () => {
    if (pendingSpacingEdit && !await pendingSpacingEdit) return;
    try {
      await listener;
      await startupDefaults;
      const message = byId("personal-defaults-status");
      await desktop.invoke("open_settings_window", {snapshot: {...settingsSnapshot(), fontChoices: documentFonts.choices(),
        connection: decisionConnection, ...(message.textContent && {defaultsMessage: message.textContent})}});
      message.textContent = "";
    } catch (error) { errorText.textContent = error.message; }
  });
} else {
  byId("open-settings").addEventListener("click", async () => {
    if (pendingSpacingEdit && !await pendingSpacingEdit) return;
    openSettingsConnection(decisionConnection);
    settingsDialog.showModal();
  });
  byId("apply-settings").addEventListener("click", () => {
    if (!connectionForm.valid()) return;
    applyConnection(connectionForm.value());
    settingsDialog.close();
  });
  settingsDialog.addEventListener("close", () => connectionForm.open(undefined));
  byId("close-settings").addEventListener("click", () => settingsDialog.close());
}
