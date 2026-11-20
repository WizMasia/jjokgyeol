"use strict";

const byId = id => document.getElementById(id);
const source = byId("source");
const preview = byId("preview");
const proofChoice = byId("proof-choice");
proofChoice.add(new Option("모델 우선", "model"));
proofChoice.add(new Option("규칙", "rule"));
const inlinePdf = navigator.pdfViewerEnabled !== false;
// A viewerless browser would download every iframe navigation automatically.
if (!inlinePdf) preview.setAttribute("sandbox", "");
const statusText = byId("status");
const errorText = byId("error");
let version = 0;
let displayedVersion = -1;
let displayedProof = null;
let pending = null;
const fileLoads = {source: null, style: null};
let initialPreviewNeeded = false;
let original = "";
let originalView = "";
let withBom = false;
let newline = "\n";
let mixedEndings = false;
let editStart = Infinity;

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

function sourceText() {
  if (source.value === originalView) return original;
  return source.value.replaceAll("\n", newline);
}

function editManuscript() {
  if (mixedEndings) {
    const view = source.value;
    const {start, end, nextEnd} = editedRange(originalView, view, Math.min(editStart, source.selectionStart));
    // Only CRLF occupies a second code unit in the file at a textarea newline.
    let rawStart = start;
    let rawEnd = end;
    for (const match of original.matchAll(/\r\n/g)) {
      if (match.index >= rawEnd) break;
      if (match.index < rawStart) rawStart++;
      rawEnd++;
    }
    original = original.slice(0, rawStart) + view.slice(start, nextEnd).replaceAll("\n", newline) + original.slice(rawEnd);
    originalView = view;
  }
  editStart = Infinity;
  edited("source");
}

const settings = JSON.parse(byId("editor-settings").textContent);
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

function decimalKey(value) {
  const raw = String(value);
  const match = /^([+-]?)(\d+(?:\.\d*)?|\.\d+)(?:[eE]([+-]?\d+))?$/.exec(raw);
  if (!match || match[0] !== raw) return null;
  const [whole, fraction = ""] = match[2].split(".");
  const digits = (whole + fraction).replace(/^0+/, "");
  if (!digits) return "0";
  const coefficient = digits.replace(/0+$/, "");
  const power = BigInt(match[3] ?? "0") + BigInt(digits.length - coefficient.length - fraction.length);
  return (match[1] === "-" ? "-" : "") + coefficient + "e" + power;
}

function sameDecimal(left, right) {
  if (left === right) return true;
  const key = decimalKey(left);
  return key !== null && key === decimalKey(right);
}

function syncPagePreset() {
  const match = Object.entries(pagePresets).find(([, [width, height]]) =>
    sameDecimal(pageWidth.value, width) && sameDecimal(pageHeight.value, height));
  pagePreset.value = match?.[0] ?? "custom";
}

function compareDecimal(left, right) {
  const a = decimalKey(left);
  const b = decimalKey(right);
  if (a === null || b === null) return NaN;
  if (a === b) return 0;
  const sign = key => key === "0" ? 0 : key.startsWith("-") ? -1 : 1;
  if (sign(a) !== sign(b)) return sign(a) - sign(b);
  // Compare the decimal point positions first, then the normalized significant digits.
  const [digitsA, powerA] = a.replace(/^-/, "").split("e");
  const [digitsB, powerB] = b.replace(/^-/, "").split("e");
  const placesA = BigInt(digitsA.length) + BigInt(powerA);
  const placesB = BigInt(digitsB.length) + BigInt(powerB);
  const smaller = placesA === placesB ? digitsA < digitsB : placesA < placesB;
  return sign(a) * (smaller ? -1 : 1);
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
    return Object.assign(values, indents.get(), typography.get());
  },
  set(values, {preserveDraft = false, inactiveArticleExpr = settings.default_article_expr ?? ""} = {}) {
    for (const [name, controls] of styleFields) {
      const value = values[name];
      controls.forEach((control, index) => {
        control.value = Array.isArray(value) ? value[index] : value;
      });
    }
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
  },
};

for (const select of stylePanel.querySelectorAll("select[name]")) {
  for (const font of settings.fonts) select.add(new Option(font.label, font.id));
}
styleForm.set({...settings.defaults, ...settings.new_spacing, text_styles: settings.new_text_styles, paragraph_layout: settings.new_paragraph_layout});
stylePanel.inert = false;

function showReferenceError() {
  errorText.textContent = "참고 서식 검토 화면을 열지 못했습니다. 다시 시도하세요.";
}

function referenceStateChanged() {
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

function changed() {
  version++;
  referenceStateChanged();
  statusText.textContent = displayedProof ? "이전 버전 · 갱신 필요" : "갱신 필요";
  byId("placeholder").textContent = "미리보기를 갱신하면 조판된 지면이 열립니다.";
}

function download(url, name) {
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
}

function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  try {
    download(url, name);
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
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
  const response = await fetch("/api/render", {
    method: "POST", headers: {"Content-Type": "application/json", Accept: "multipart/form-data"}, signal,
    body: JSON.stringify({source: sourceText(), style}), redirect: "error",
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    const error = new Error(detail?.message || httpMessages[response.status] || "조판을 마치지 못했습니다. 다시 시도하세요.");
    error.field = detail?.field;
    throw error;
  }
  const type = response.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase();
  const decision = response.headers.get("X-Jjokgyeol-Decision");
  const validPdf = file => file instanceof Blob && file.type.split(";")[0] === "application/pdf" && file.size > 0;
  if (type === "application/pdf" && (decision === null || decision === "rule")) {
    const rule = await response.blob();
    if (validPdf(rule)) return {rule};
  }
  if (type === "multipart/form-data" && (decision === "model" || decision === "mixed")) {
    const parts = await response.formData().catch(() => null);
    const rule = parts?.get("rule");
    const model = parts?.get("model");
    if (validPdf(rule) && validPdf(model)) return {rule, model, decision};
  }
  throw new Error("서버에서 올바른 조판 PDF를 받지 못했습니다. 다시 갱신하세요.");
}

// Only current responses acquire URLs. A reference trial transfers these URLs on apply.
class ProofUrls {
  constructor({rule, model, decision}) {
    this.rule = URL.createObjectURL(rule);
    this.model = model && URL.createObjectURL(model);
    this.decision = decision;
  }

  get selected() {
    const useModel = this.model && proofChoice.value === "model";
    let label = this.model ? "규칙 조판" : "규칙 조판 · 모델 결과 없음";
    if (useModel) label = this.decision === "mixed" ? "혼합 조판 · 일부 문단에 모델 적용" : "모델 조판";
    return {href: (useModel ? this.model : this.rule) + "#navpanes=0&view=FitH", label};
  }

  release() {
    URL.revokeObjectURL(this.rule);
    if (this.model) URL.revokeObjectURL(this.model);
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
  if (preview.src !== href) preview.src = href;
  byId("proof-decision").textContent = "표시 중 · " + label;
}

proofChoice.addEventListener("change", displaySelectedProof);

function showProof(proof, style) {
  const previous = displayedProof;
  displayedProof = proof;
  displaySelectedProof();
  previous?.release();
  preview.hidden = !inlinePdf;
  byId("viewer-fallback").hidden = inlinePdf;
  byId("placeholder").hidden = true;
  displayedVersion = version;
  byId("proof-size").textContent = `${style.page_mm.join(" × ")} mm · 본문 ${style.body_pt} pt`;
  byId("applied-ratios").textContent = spacingCaption(style);
  statusText.textContent = "최신 버전";
}

async function render(job) {
  const style = styleForm.get();
  try {
    const files = await fetchProof(style);
    if (pending !== job || version !== job.version) return;
    showProof(new ProofUrls(files), style);
    if (job.exporting) exportPdf();
  } catch (error) {
    if (pending !== job || version !== job.version) return;
    const field = error.field;
    const message = error instanceof TypeError ? "조판에 실패했습니다. 연결을 확인하고 다시 시도하세요." : error.message;
    if (field === "article_expr") articleError(message);
    marginError(field, message);
    typography.error(field, message);
    errorText.textContent = field ? `${field} · ${message}` : message;
    statusText.textContent = displayedVersion === version ? "현재 지면 · 갱신 실패" :
      displayedProof ? "이전 버전 · 조판 실패" : "조판 실패";
    byId("placeholder").textContent = "원고와 양식을 확인한 뒤 미리보기를 갱신하세요.";
  } finally {
    if (pending === job) pending = null;
  }
}

function requestPdf(exporting) {
  if (fileLoads.source || fileLoads.style) return;
  if (pending?.version === version) {
    pending.exporting ||= exporting;
    return;
  }
  if (exporting && displayedVersion === version) {
    exportPdf();
    return;
  }
  pending = {version, exporting};
  errorText.textContent = "";
  marginError();
  statusText.textContent = displayedProof && displayedVersion !== version ? "이전 버전 · 조판 중" : "조판 중";
  byId("placeholder").textContent = "첫 지면을 조판하고 있습니다.";
  render(pending);
}

function updateFileActions() {
  const busy = Boolean(fileLoads.source || fileLoads.style);
  byId("refresh").disabled = busy;
  byId("export").disabled = busy;
  byId("save").disabled = Boolean(fileLoads.source);
  pinButton.disabled = Boolean(fileLoads.source);
  byId("save-style").disabled = Boolean(fileLoads.style);
  if (busy) statusText.textContent = fileLoads.style ? "양식 불러오는 중" : "원고 처리 중";
}

function beginFileOpen(kind) {
  if (kind === "style") document.dispatchEvent(new Event("style-suggestion-invalidate"));
  const token = Symbol();
  fileLoads[kind] = token;
  referenceStateChanged();
  pending = null;
  errorText.textContent = "";
  updateFileActions();
  return token;
}

function finishFileOpen(kind, token) {
  if (fileLoads[kind] !== token) return;
  fileLoads[kind] = null;
  referenceStateChanged();
  updateFileActions();
  if (fileLoads.source || fileLoads.style) return;
  statusText.textContent = displayedVersion === version ? "최신 버전" :
    displayedProof ? "이전 버전 · 갱신 필요" : "갱신 필요";
  if (initialPreviewNeeded) {
    initialPreviewNeeded = false;
    if (!errorText.textContent) requestPdf(false);
  }
}

async function openFile(kind, read, apply, onError) {
  const turn = beginFileOpen(kind);
  try {
    const data = await read();
    if (fileLoads[kind] === turn) {
      apply(data);
      changed();
    }
  } catch (error) {
    if (fileLoads[kind] === turn) onError(error);
  } finally {
    finishFileOpen(kind, turn);
  }
}

async function readMarkdown(file) {
  const input = file || await fetch("/sample.md", {redirect: "error"});
  if (!file && !input.ok) throw new Error();
  return new TextDecoder("utf-8", {fatal: true, ignoreBOM: true}).decode(await input.arrayBuffer());
}

function applyManuscript(text, name, warnings = []) {
  withBom = text.startsWith("\uFEFF");
  const manuscript = withBom ? text.slice(1) : text;
  source.value = manuscript;
  rememberSource(manuscript);
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

function openMarkdown(file) {
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
  });
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

function importManuscript(file) {
  initialPreviewNeeded = false;
  return openFile("source", () => requestImport(file), ({markdown, warnings}) => {
    applyManuscript(markdown, file.name.replace(/\.[^.]*$/, "") + ".md", warnings);
  }, error => {
    errorText.textContent = error instanceof TypeError ? "연결을 확인하고 원고 가져오기를 다시 시도하세요." : error.message;
  });
}

function edited(kind) {
  if (kind === "style") {
    typography.clearError();
    document.dispatchEvent(new Event("style-suggestion-invalidate"));
  }
  fileLoads[kind] = null;
  initialPreviewNeeded = false;
  errorText.textContent = "";
  changed();
  updateFileActions();
}

async function requestPin() {
  const start = source.selectionStart;
  const end = source.selectionEnd;
  const lineAt = index => source.value.slice(0, index).split("\n").length - 1;
  const response = await fetch("/api/article/pin", {
    method: "POST", redirect: "error", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({source: sourceText(), first_line: lineAt(start), last_line: lineAt(Math.max(start, end - 1))}),
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
  if (document.activeElement === document.body || document.activeElement === pinButton) source.focus({preventScroll: true});
}

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
      openFile("style", () => validatedMarginRestore(draft, geometry, gutter_sp), applyMarginGeometry, onError);
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
    openFile("style", () => fixedMargins(geometry), apply, onError);
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
byId("save").addEventListener("click", () => {
  const blob = new Blob([withBom ? "\uFEFF" : "", sourceText()], {type: "text/markdown;charset=utf-8"});
  saveBlob(blob, byId("filename").textContent);
});
openMarkdown(null);
