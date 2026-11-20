"use strict";

const panel = byId("reference-review");
const fileInput = byId("reference-file");
const message = byId("reference-message");
const start = byId("reference-start");
const end = byId("reference-end");
const section = byId("reference-section");
const width = byId("reference-width");
const roles = {document: "문서", heading: "제목", body: "본문", list: "목록", header: "머리글", footer: "바닥글"};
const names = {
  page_mm: "판형 (폭, 높이 mm)", margins_mm: "여백 (위, 오른쪽, 아래, 왼쪽 mm)",
  font_pt: "글자 크기 (pt)", leading_pt: "행간 (pt)", font: "글꼴", color: "글자 색",
  first_indent_mm: "첫 줄 들여쓰기 (mm)", rest_indent_mm: "이후 줄 들여쓰기 (mm)",
  header: "머리글", footer: "바닥글",
  left: "왼쪽 칸", center: "가운데 칸", right: "오른쪽 칸", visible: "표시 여부", offset_mm: "종이 끝 거리 (mm)",
};
const basisNames = {read: "읽음", estimated: "추정", confirm: "확인 필요", unsupported: "미지원"};
const levelKey = /^levels\.([1-9][0-9]*)\.(expr|indent_mm|gap_mm)$/;
const levelNames = {expr: "표지 표현식", indent_mm: "들여쓰기 (mm)", gap_mm: "표지 뒤 간격 (mm)"};
const documentKeys = ["page_mm", "margins_mm"];
let mirroredReview = styleForm.get().gutter_mm !== undefined;
const globalPath = "[]";

let config;
let units = [];
let analysis;
let targets = [];
let targetsFor;
let targetRequest;
let targetTimer;
let savedRules = "";
let bindings = [];
let attempt;
let proof;
const choices = new Map();
const widths = new Map();
const unitsPerView = 12;
let firstVisible = 0;
const runningKeys = ["left", "center", "right", "font", "font_pt", "color", "offset_mm"];
const variants = {default: "기본", first: "첫 쪽", even: "짝수 쪽", odd: "홀수 쪽", unknown: "변형 미확인"};
const faces = {default: "공통", first: "첫 쪽", odd: "홀수 쪽", even: "짝수 쪽"};
const runningEdits = new Map();
let areaPath = globalPath;
let areaRole = "header";
let areaFace = "default";
const area = byId("running-editor");

function isRunning(role) { return role === "header" || role === "footer"; }

function areaId(path, role, face) { return JSON.stringify([path, role, face]); }

function newRule(path, role, face, values) {
  const rule = {path: JSON.parse(path), role, values};
  if (face !== "default") rule.face = face;
  return rule;
}

function node(tag, text) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  return element;
}

function labelled(name, control) {
  const label = node("label", name + " ");
  control.setAttribute("aria-label", name);
  label.append(control);
  return label;
}

function busy() {
  return Boolean(fileLoads.source || fileLoads.style);
}

async function jsonRequest(url, options) {
  const response = await fetch(url, {redirect: "error", ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || "참고 서식을 처리하지 못했습니다.");
  return data;
}

function discardProof() {
  if (attempt) attempt.abort();
  attempt = null;
  byId("reference-proof").replaceChildren();
  if (proof) URL.revokeObjectURL(proof.url);
  proof = null;
  byId("reference-apply").disabled = true;
}

function clearRunningError() {
  byId("running-error").textContent = "";
  area.querySelectorAll("[aria-invalid=true]").forEach(field => {
    field.removeAttribute("aria-invalid");
    field.setAttribute("aria-describedby", field.id.replace("running-", "running-parity-"));
  });
}

function revised() {
  discardProof();
  clearRunningError();
  message.textContent = "";
  limits();
}

function limits() {
  if (panel.hidden) return;
  byId("reference-analyze").disabled = !config || !fileInput.files.length || Boolean(analysis);
  byId("reference-trial").disabled = busy() || targetsFor !== sourceText() || Boolean(analysis) || Boolean(attempt);
}

function targetOptions(selected, change, editsDraft = true) {
  const select = node("select");
  select.add(new Option("문서 전체", globalPath));
  for (const {path, count} of targets) {
    if (!path.length) continue;
    const option = new Option(path.join(" › ") + (count === 1 ? "" : " (중복)"), JSON.stringify(path));
    option.disabled = count !== 1;
    select.add(option);
  }
  if (![...select.options].some(option => option.value === selected)) {
    const option = new Option(JSON.parse(selected).join(" › ") + " (찾을 수 없음)", selected);
    option.disabled = true;
    select.add(option);
  }
  select.value = selected;
  select.addEventListener("input", () => {
    if (editsDraft) revised();
    change(select.value);
  });
  return select;
}

function bound(path) {
  return path === globalPath || targets.some(t => t.count === 1 && JSON.stringify(t.path) === path);
}

function editedValues(rule, patch) {
  const values = {...rule?.values};
  for (const [key, value] of Object.entries(patch)) {
    if (value === null) delete values[key];
    else values[key] = value;
  }
  return values;
}

function drawBindings() {
  const list = byId("reference-bindings");
  list.replaceChildren();
  if (!bindings.length) return;
  list.append(node("h3", "구간 서식 연결"));
  for (const binding of bindings) {
    const row = node("div");
    row.className = "reference-row";
    const old = binding.rule.path.join(" › ") || "문서 전체";
    const face = isRunning(binding.rule.role) ? " · " + faces[binding.rule.face ?? "default"] : "";
    row.append(node("span", old + " · " + roles[binding.rule.role] + face + " · " +
      Object.entries(binding.rule.values).map(([key, value]) => keyName(key) + " " + value).join(", ")));
    const select = targetOptions(binding.path, path => {
      const face = binding.rule.face ?? "default";
      const id = areaId(binding.path, binding.rule.role, face);
      const edit = runningEdits.get(id);
      if (edit) {
        const values = editedValues(binding.rule, edit.values);
        binding.rule = {...binding.rule, values};
        binding.remove = !Object.keys(values).length;
        runningEdits.delete(id);
      }
      if (binding.path === areaPath && binding.rule.role === areaRole && face === areaFace) areaPath = path;
      binding.path = path;
      drawBindings();
      drawRunning();
    });
    row.append(labelled("다시 연결할 대상", select));
    const remove = node("input");
    remove.type = "checkbox";
    remove.checked = binding.remove;
    select.disabled = binding.remove;
    remove.addEventListener("input", () => {
      revised(); binding.remove = remove.checked; select.disabled = remove.checked; drawRunning();
    });
    row.append(labelled("규칙 제외", remove));
    list.append(row);
  }
}

function refreshBindings() {
  const signature = JSON.stringify(sectionRules ?? []);
  if (signature !== savedRules) {
    bindings = (sectionRules || []).map(rule => ({rule, path: JSON.stringify(rule.path), remove: false}));
    savedRules = signature;
  }
  const missing = (sectionRules || []).filter(rule => !bound(JSON.stringify(rule.path)));
  byId("reference-notice").textContent = missing.length && targetsFor === sourceText()
    ? `구간 서식 ${missing.length}개의 제목이 없거나 중복됩니다. 참고 문서 서식 가져오기에서 적용 대상을 다시 연결하세요.` : "";
}

function rebindMissingEdits() {
  for (const [id, edit] of runningEdits) {
    if (bound(edit.path)) continue;
    const binding = bindings.find(b => !b.remove && b.path === edit.path && b.rule.role === edit.role &&
      (b.rule.face ?? "default") === edit.face);
    const values = editedValues(binding?.rule, edit.values);
    if (binding) {
      binding.rule = {...binding.rule, values};
      binding.remove = !Object.keys(values).length;
    } else if (Object.keys(values).length) {
      const rule = newRule(edit.path, edit.role, edit.face, values);
      bindings.push({rule, path: edit.path, remove: false});
    }
    runningEdits.delete(id);
  }
}

async function readTargets(force = false) {
  clearTimeout(targetTimer);
  if (busy() || (panel.hidden && !sectionRules?.length)) return;
  const text = sourceText();
  if (!force && targetsFor === text) { refreshBindings(); draw(); return; }
  if (targetRequest?.source === text) return;
  targetRequest?.controller.abort();
  const turn = {source: text, controller: new AbortController()};
  targetRequest = turn;
  targetsFor = undefined;
  limits();
  try {
    const result = await jsonRequest("/api/reference/targets", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({source: text}),
      signal: turn.controller.signal,
    });
    if (targetRequest !== turn || text !== sourceText() || busy()) return;
    targets = result.targets;
    targetsFor = text;
    refreshBindings();
    rebindMissingEdits();
    draw();
  } catch (error) {
    if (targetRequest === turn) {
      const warning = "적용 대상을 읽지 못했습니다. " + error.message;
      message.textContent = warning;
      if (panel.hidden) byId("reference-notice").textContent = warning;
    }
  } finally {
    if (targetRequest === turn) targetRequest = null;
    limits();
  }
}

export function editorChanged() {
  discardProof();
  clearTimeout(targetTimer);
  if (targetRequest && (busy() || targetRequest.source !== sourceText())) {
    targetRequest.controller.abort();
    targetRequest = null;
  }
  refreshBindings();
  const mirrored = styleForm.get().gutter_mm !== undefined;
  if (mirrored !== mirroredReview) {
    mirroredReview = mirrored;
    if (mirrored) {
      for (const [candidate, choice] of choices) {
        if (candidate.role === "document" && candidate.key === "margins_mm") choice.checked = false;
      }
    }
    if (!panel.hidden) draw();
  }
  if (!panel.hidden) message.textContent = "현재 원고와 양식으로 다시 시험 조판하세요.";
  if (!busy() && (!panel.hidden || sectionRules?.length)) targetTimer = setTimeout(readTargets, 150);
  limits();
}

document.addEventListener("reference-editor-change", editorChanged);

function keyName(key) {
  const match = levelKey.exec(key);
  return match ? `${match[1]}단계 ${levelNames[match[2]]}` : names[key] ?? key;
}

function permittedRoles(key) {
  if (documentKeys.includes(key)) return ["document"];
  if (levelKey.test(key)) return ["list"];
  if (["first_indent_mm", "rest_indent_mm"].includes(key)) return ["body", "list"];
  return ["font_pt", "leading_pt", "font", "color"].includes(key) ? ["heading", "body", "list"] : [];
}

function suggestedFace(candidate) {
  return candidate.suggested_face === undefined ? "default" : candidate.suggested_face;
}

function choiceFor(candidate) {
  if (!choices.has(candidate)) choices.set(candidate, {checked: false, path: globalPath, role: candidate.role,
    face: suggestedFace(candidate)});
  return choices.get(candidate);
}

function inheritedValue(style, path, role, key, face = "default", parity = face === "first" ? "odd" : face, firstDepth) {
  const parts = JSON.parse(path);
  let priority = -1;
  let value;
  let applies = false;
  for (const rule of style.section_rules || []) {
    if (role !== rule.role || !bound(JSON.stringify(rule.path))) continue;
    if (rule.path.every((part, i) => part === parts[i]) &&
        (role !== "heading" || !rule.path.length || rule.path.length === parts.length)) {
      const layer = rule.face ?? "default";
      let rank = 0;
      if (layer !== "default") {
        if (layer === parity) rank = 1;
        else if (layer === "first" && face === "first" && rule.path.length >= (firstDepth ?? parts.length)) rank = 2;
        else continue;
      }
      applies = true;
      const order = 3 * rule.path.length + rank;
      if (!(key in rule.values) || order < priority) continue;
      value = rule.values[key];
      priority = order;
    }
  }
  if (applies && value === undefined && isRunning(role) && key === "visible") return Boolean(settings.running_defaults?.visible);
  return value;
}

function currentValue(style, path, role, key, face, parity, firstDepth) {
  const value = inheritedValue(style, path, role, key, face, parity, firstDepth);
  if (value !== undefined) return value;
  if (isRunning(role)) return key === "visible" ? false : settings.running_defaults?.[key];
  const headingLevel = role === "heading" && targets.find(t => t.count === 1 && JSON.stringify(t.path) === path)?.level;
  const textRole = headingLevel ? "heading" + headingLevel : ["body", "list"].includes(role) ? "body" : null;
  const direct = style.text_styles?.[textRole];
  if (direct?.[key] !== undefined) return direct[key];
  if (role === "heading" && ["font_pt", "leading_pt"].includes(key)) {
    const level = headingLevel;
    const scale = config?.heading_per_10000?.[level - 1];
    if (!scale || !config.heading_leading_per_10000) return "제목 단계별 자동";
    // The engine rounds to a TeX scaled point after each multiplication.
    const sp = 65536;
    const size = inheritedValue(style, path, role, "font_pt") ?? direct?.font_pt;
    const sizeSp = size === undefined ? Math.round(Math.round(Number(style.body_pt) * sp) * scale / 10000)
      : Math.round(Number(size) * sp);
    const measured = key === "font_pt" ? sizeSp : Math.round(sizeSp * config.heading_leading_per_10000 / 10000);
    return measured > 0 && Number.isFinite(measured) ? Number((measured / sp).toFixed(4)) : "본문 크기 확인 필요";
  }
  if (role === "list" && ["first_indent_mm", "rest_indent_mm"].includes(key)) return "0";
  if (Object.hasOwn(style, key)) return style[key];
  if (key === "font") return style[role === "heading" ? "heading_font" : "body_font"];
  if (key === "font_pt") return style.body_pt;
  if (key === "color") return settings.text_defaults?.color ?? "#000000";
  const level = levelKey.exec(key);
  if (level) {
    const levels = style.list_levels || settings.list_presets?.[settings.default_list_preset] || [];
    return levels[Math.min(levels.length, Number(level[1])) - 1]?.[level[2]];
  }
}

// Before typesetting, a title may start on either parity and share its first page with its ancestors.
function runningCases(style, path, role, face) {
  if (face !== "first" || path === globalPath) return [{parities: [face === "first" ? "odd" : face]}];
  const parts = JSON.parse(path);
  const cases = [{parities: ["odd", "even"]}];
  for (const rule of style.section_rules || []) {
    if (rule.role !== role || rule.face !== "first" || rule.path.length >= parts.length ||
        !rule.path.every((s, i) => s === parts[i]) || !bound(JSON.stringify(rule.path))) continue;
    cases.push({label: (rule.path.join(" › ") || "문서") + " 첫 쪽과 겹치면",
      firstDepth: rule.path.length, parities: rule.path.length ? ["odd", "even"] : ["odd"]});
  }
  return cases;
}

// A more specific title would override its parent's cover or parity-specific hide.
function overlapsParentHide(rule, parts, role, face) {
  if (rule.values.visible !== false || rule.role !== role || rule.path.length >= parts.length ||
      !rule.path.every((part, i) => part === parts[i]) || !bound(JSON.stringify(rule.path))) return false;
  const parentFace = rule.face === "first" && !rule.path.length ? "odd" : rule.face;
  return !((parentFace === "even" && face === "odd") || (parentFace === "odd" && face === "even"));
}

function runningAlternatives(style, path, role, key, face, cases) {
  const format = value => {
    if (key === "visible") return value ? "표시" : "숨김";
    if (key === "font") return settings.fonts.find(font => font.id === value)?.label ?? value;
    return value === "" ? "빈칸" : value;
  };
  const results = cases.map(({firstDepth, parities}) => parities.map(parity =>
    currentValue(style, path, role, key, face, parity, firstDepth)));
  const describe = (values, parities) => values.length === 2 && values[0] !== values[1]
    ? values.map((value, i) => faces[parities[i]] + " " + format(value)).join(" · ") : format(values[0]);
  const base = results[0];
  const alternatives = [];
  for (let i = 1; i < cases.length; i++) {
    if (results[i].some((value, j) => value !== base[j])) {
      alternatives.push(cases[i].label + " " + describe(results[i], cases[i].parities));
    }
  }
  if (alternatives.length) return ["상위 첫 면과 겹치지 않으면 " + describe(base, cases[0].parities), ...alternatives].join(" / ");
  return base.length === 2 && base[0] !== base[1] ? describe(base, cases[0].parities) : "";
}

function calibratedWidth(unit) {
  if (widths.has(unit)) return widths.get(unit);
  const page = unit.candidates.find(c => c.role === "document" && c.key === "page_mm" &&
    c.basis === "read" && c.unit !== "width_per_10000" && Array.isArray(c.value));
  const mm = Number(page?.value[0]);
  return Number.isFinite(mm) && mm > 0 ? String(page.value[0]) : "";
}

function proposed(candidate, unit, choice) {
  if (choice.edited !== undefined) return choice.edited;
  const raw = candidate.value;
  if (candidate.key === "font") {
    return settings.fonts.find(font => font.id === raw || font.label === raw)?.id || "";
  }
  if (candidate.unit !== "width_per_10000") return Array.isArray(raw) ? raw.join(", ") : String(raw ?? "");
  const mm = Number(calibratedWidth(unit));
  if (!(mm > 0) || !Number.isFinite(mm)) return "";
  const factor = candidate.key.endsWith("_pt") ? 72.27 / 25.4 : 1;
  const convert = number => String(Number((Number(number) * mm * factor / 10000).toFixed(10)));
  return Array.isArray(raw) ? raw.map(convert).join(", ") : convert(raw);
}

function showArea(path, role, face = areaFace) {
  if (path !== areaPath || role !== areaRole || face !== areaFace) clearRunningError();
  areaPath = path;
  areaRole = role;
  areaFace = face;
  area.hidden = false;
  byId("open-running").setAttribute("aria-expanded", "true");
  drawRunning();
}

function matchingChoices(key) {
  return inRange().flatMap(unit => unit.candidates).filter(c => {
    const choice = choices.get(c);
    return c.role === areaRole && c.key === key && choice?.checked && choice.path === areaPath && choice.face === areaFace;
  });
}

function editArea(key, value) {
  revised();
  const id = areaId(areaPath, areaRole, areaFace);
  const edit = runningEdits.get(id) ?? {path: areaPath, role: areaRole, face: areaFace, values: {}};
  const candidates = matchingChoices(key);
  if (candidates.length === 1 && value !== null) {
    choiceFor(candidates[0]).edited = value;
  } else {
    for (const c of candidates) choiceFor(c).checked = false;
    edit.values[key] = value;
  }
  runningEdits.set(id, edit);
  draw();
}

function initRunning() {
  const face = byId("running-face");
  for (const [id, name] of Object.entries(faces)) face.add(new Option(name, id));
  face.addEventListener("input", () => showArea(areaPath, areaRole, face.value));
  const role = byId("running-role");
  for (const id of ["header", "footer"]) role.add(new Option(roles[id], id));
  role.addEventListener("input", () => showArea(areaPath, role.value));
  const visible = byId("running-visible");
  for (const [value, name] of [["inherit", "상속"], ["show", "표시"], ["hide", "숨김"]]) visible.add(new Option(name, value));
  visible.addEventListener("input", () => editArea("visible", visible.value === "inherit" ? null : visible.value === "show"));
  for (const key of runningKeys) {
    const field = node("fieldset");
    const legend = node("legend", names[key]);
    const input = node(key === "font" ? "select" : "input");
    input.id = "running-" + key;
    input.setAttribute("aria-label", names[key]);
    if (key === "font") {
      for (const font of settings.fonts) input.add(new Option(font.label, font.id));
    } else {
      input.type = "text";
      if (key === "font_pt" || key === "offset_mm") input.inputMode = "decimal";
      input.spellcheck = false;
    }
    const own = node("input");
    own.type = "checkbox";
    own.id = "running-own-" + key;
    own.addEventListener("input", () => editArea(key, own.checked ? input.value : null));
    input.addEventListener("input", () => editArea(key, input.value));
    const parity = node("small");
    parity.id = "running-parity-" + key;
    input.setAttribute("aria-describedby", parity.id);
    field.append(legend, labelled("직접 지정", own), input, parity);
    byId("running-fields").append(field);
  }
}

function drawRunning() {
  if (area.hidden) return;
  const suggestions = new Set();
  const style = draftStyle(false, suggestions);
  const path = targetOptions(areaPath, p => showArea(p, areaRole), false);
  path.id = "running-target";
  byId("running-target").replaceWith(path);
  byId("running-role").value = areaRole;
  byId("running-face").value = areaFace;
  const own = style.section_rules?.find(rule => rule.role === areaRole && JSON.stringify(rule.path) === areaPath &&
    (rule.face ?? "default") === areaFace)?.values ?? {};
  const withoutVisibility = style.section_rules?.flatMap(rule => {
    if (rule.values !== own || !Object.hasOwn(own, "visible")) return [rule];
    const {visible, ...values} = own;
    return Object.keys(values).length ? [{...rule, values}] : [];
  });
  const cases = runningCases(style, areaPath, areaRole, areaFace);
  function fieldValue(source, key) { return currentValue(source, areaPath, areaRole, key, areaFace); }
  function alternatives(source, key) {
    return areaFace === "first" && areaPath !== globalPath
      ? runningAlternatives(source, areaPath, areaRole, key, areaFace, cases) : "";
  }
  const parentStyle = {section_rules: withoutVisibility};
  const inherited = fieldValue(parentStyle, "visible");
  const visible = byId("running-visible");
  visible.options[0].textContent = "상속 (" + (alternatives(parentStyle, "visible") || (inherited ? "표시" : "숨김")) + ")";
  visible.value = own.visible === undefined ? "inherit" : own.visible ? "show" : "hide";
  const effective = fieldValue(style, "visible");
  const possible = alternatives(style, "visible");
  byId("running-state").textContent = roles[areaRole] + " " + (possible ? possible + " · 실제 면은 시험 PDF에서 확인하세요." : effective ? "표시" : "숨김") +
    (areaRole === "header" ? " · 위쪽 종이 끝에서 머리글 위까지" : " · 아래쪽 종이 끝에서 바닥글 아래까지") +
    (suggestions.has(areaId(areaPath, areaRole, areaFace)) ? " · 참고 후보를 선택해 이 영역의 표시도 함께 제안합니다." : "");
  for (const key of runningKeys) {
    const specified = Object.hasOwn(own, key);
    byId("running-own-" + key).checked = specified;
    const field = byId("running-" + key);
    field.disabled = !specified;
    const parity = byId("running-parity-" + key);
    parity.textContent = alternatives(style, key);
    parity.hidden = !parity.textContent;
    const text = String(fieldValue(style, key) ?? "");
    if (field.value !== text) field.value = text;
  }
}

initRunning();

function candidateRow(candidate, unit, style) {
  const {role, key, basis, evidence} = candidate;
  const choice = choiceFor(candidate);
  const row = node("div");
  Object.assign(row.dataset, {role, key});
  row.className = "reference-row";
  const running = isRunning(role);
  const allowed = running ? (runningKeys.includes(key) || key === "visible" ? [role] : []) : permittedRoles(key);
  if (running) {
    row.dataset.sourceGroup = candidate.source_group;
    row.dataset.variant = candidate.variant;
  }
  const fixedOnly = style.gutter_mm !== undefined && role === "document" && key === "margins_mm";
  const unsupported = basis === "unsupported" || !allowed.includes(role);
  function syncArea() {
    if (!running) return;
    if (choice.checked && choice.face) showArea(choice.path, role, choice.face);
    else drawRunning();
  }
  const check = node("input");
  check.type = "checkbox";
  check.checked = choice.checked;
  check.disabled = unsupported || fixedOnly;
  check.addEventListener("input", () => {
    revised();
    choice.checked = check.checked;
    syncArea();
  });
  row.append(labelled((roles[role] || role) + " · " + keyName(key), check));
  if (running) row.append(node("small", "출처 면 · " + (variants[candidate.variant] || variants.unknown) + " · " + candidate.source_group));
  const ratio = candidate.unit === "width_per_10000" ? " · 지면 폭의 1/10,000 단위" : "";
  row.append(node("small", `${unsupported ? "미지원" : basisNames[basis] || "확인 필요"} · ${candidate.value ?? ""}${ratio} · ${evidence}`));
  for (const earlier of choice.previousEvidence ?? []) {
    if (earlier !== evidence) row.append(node("small", "이전 근거 · " + earlier));
  }
  if (fixedOnly) row.append(node("small", "고정 좌우 여백에서 적용할 수 있습니다."));
  if (unsupported || fixedOnly) return row;

  if (running) {
    const face = node("select");
    face.add(new Option("선택하세요", ""));
    for (const [id, name] of Object.entries(faces)) face.add(new Option(name, id));
    face.value = choice.face ?? "";
    face.addEventListener("input", () => {
      revised();
      choice.face = face.value || null;
      choice.faceChosen = true;
      showCurrent();
      syncArea();
    });
    row.append(labelled("적용할 면", face));
  }
  const value = node(["font", "visible"].includes(key) ? "select" : "input");
  if (key === "visible") {
    value.add(new Option("표시", "true"));
    value.add(new Option("숨김", "false"));
  } else if (key === "font") {
    value.add(new Option("지원 글꼴 선택", ""));
    for (const font of settings.fonts) value.add(new Option(font.label, font.id));
  } else {
    value.type = "text";
    if (key.endsWith("_pt") || key.endsWith("_mm") && !documentKeys.includes(key)) value.inputMode = "decimal";
  }
  value.value = proposed(candidate, unit, choice);
  value.addEventListener("input", () => {
    revised(); choice.edited = value.value;
    syncArea();
  });
  if (candidate.options?.length && running) {
    const select = node("select");
    select.add(new Option("선택하세요", ""));
    for (const option of candidate.options) {
      select.add(new Option((option.kind === "page" ? "자동 쪽 번호" : "원문 숫자") + " · " + option.value, option.kind));
    }
    select.value = choice.option ?? "";
    select.addEventListener("input", () => {
      revised();
      choice.option = select.value;
      choice.edited = candidate.options.find(o => o.kind === select.value)?.value;
      value.value = proposed(candidate, unit, choice);
      syncArea();
    });
    row.append(labelled("번호 해석", select));
  }
  if (choice.retry) {
    const fresh = choice.retry.candidate;
    row.append(node("small", fresh ? "재분석 · " + fresh.value + " · " + basisNames[fresh.basis] + " · " + fresh.evidence
      : "재분석 · 이 출처의 후보를 다시 찾지 못했습니다."));
    const keep = node("button", "기존 선택 유지");
    keep.addEventListener("click", () => { revised(); delete choice.retry; draw(); });
    row.append(keep);
    if (fresh) {
      const replace = node("button", "재분석 값 사용");
      replace.addEventListener("click", () => {
        revised();
        unit.candidates[unit.candidates.indexOf(candidate)] = fresh;
        choices.set(fresh, {checked: choice.checked, path: choice.path, role, faceChosen: choice.faceChosen,
          face: choice.checked || choice.faceChosen ? choice.face : suggestedFace(fresh)});
        choices.delete(candidate);
        draw();
      });
      row.append(replace);
    }
  }
  const current = node("span");
  function showCurrent() {
    if (running && !choice.face) { current.textContent = "현재 값 · 적용할 면 선택 필요"; return; }
    const old = currentValue(style, choice.path, choice.role, key, choice.face);
    const label = key === "font" ? settings.fonts.find(font => font.id === old)?.label : null;
    const possible = choice.face === "first" && choice.path !== globalPath &&
      runningAlternatives(style, choice.path, role, key, choice.face, runningCases(style, choice.path, role, choice.face));
    current.textContent = "현재 값 · " + (possible || (label ?? old ?? "기본값"));
  }
  showCurrent();
  row.append(current, labelled("제안 값", value));
  const roleSelect = node("select");
  for (const r of allowed) roleSelect.add(new Option(roles[r], r));
  roleSelect.value = choice.role;
  roleSelect.disabled = allowed.length === 1;
  roleSelect.addEventListener("input", () => { revised(); choice.role = roleSelect.value; showCurrent(); });
  const path = targetOptions(choice.path, p => {
    choice.path = p; showCurrent();
    syncArea();
  });
  path.disabled = role === "document";
  row.append(labelled("적용 요소", roleSelect), labelled("적용 대상", path));
  return row;
}

function inRange() { return units.slice(Number(start.value), Number(end.value) + 1); }

function sampleExcerpt(unit) {
  const excerpt = node("p", unit.excerpt);
  excerpt.className = "reference-excerpt";
  if (unit.preview || !unit.excerpt) return excerpt;
  const sample = unit.candidates.filter(c => ["font", "font_pt", "leading_pt", "color"].includes(c.key) &&
    c.basis !== "unsupported");
  const role = sample[0]?.role;
  const value = key => sample.find(c => c.role === role && c.key === key && c.unit !== "width_per_10000")?.value;
  const size = Number(value("font_pt")), leading = Number(value("leading_pt"));
  if (size > 0) excerpt.style.fontSize = Math.min(size, 48) + "pt";
  if (leading > 0) excerpt.style.lineHeight = Math.min(leading, 72) + "pt";
  if (/^#[0-9a-f]{6}$/i.test(value("color"))) excerpt.style.color = value("color");
  const font = settings.fonts.find(f => f.id === value("font") || f.label === value("font"));
  if (font) excerpt.style.fontFamily = '"' + font.label + '", var(--serif)';
  return excerpt;
}

function draw() {
  limits();
  if (panel.hidden) return;
  drawBindings();
  drawRunning();
  const container = byId("reference-candidates");
  container.replaceChildren();
  const style = styleForm.get();
  const selected = inRange();
  const scans = selected.filter(unit => unit.candidates.some(c => c.unit === "width_per_10000"));
  byId("reference-width-label").hidden = !scans.length;
  width.value = scans.length && scans.every(unit => calibratedWidth(unit) === calibratedWidth(scans[0]))
    ? calibratedWidth(scans[0]) : "";
  const lastVisible = Math.min(firstVisible + unitsPerView, selected.length);
  byId("reference-pages").hidden = selected.length <= unitsPerView;
  byId("reference-position").textContent = `${firstVisible + 1}–${lastVisible} / ${selected.length}`;
  byId("reference-prev").disabled = firstVisible === 0;
  byId("reference-next").disabled = lastVisible >= selected.length;
  const fragment = document.createDocumentFragment();
  for (const unit of selected.slice(firstVisible, lastVisible)) {
    const block = node("section");
    block.className = "reference-unit";
    block.append(node("h3", [unit.section, unit.location].filter(Boolean).join(" · ")));
    if (/^data:image\/(png|jpeg|webp);base64,/i.test(unit.preview || "")) {
      const img = node("img");
      img.src = unit.preview;
      img.alt = unit.location + " 참고 지면";
      block.append(img);
    }
    block.append(sampleExcerpt(unit));
    for (const candidate of unit.candidates) block.append(candidateRow(candidate, unit, style));
    if (Number.isInteger(unit.page)) {
      const retry = node("button", unit.page + "쪽 다시 분석");
      retry.disabled = Boolean(analysis);
      retry.addEventListener("click", () => analyze(unit.page));
      block.append(retry);
    }
    fragment.append(block);
  }
  container.append(fragment);
}

function setRange(first, last, position = 0) {
  firstVisible = position;
  start.value = String(first);
  end.value = String(last);
  const range = first + "-" + last;
  section.value = [...section.options].some(option => option.value === range) ? range : "";
  revised();
  draw();
}

function ranges(first = 0, last, position = 0) {
  byId("reference-range").hidden = !units.length;
  for (const select of [start, end, section]) select.replaceChildren();
  if (!units.length) { draw(); return; }
  section.add(new Option("사용자 범위", ""));
  for (let i = 0; i < units.length; i++) {
    const label = units[i].location || String(i + 1);
    for (const select of [start, end]) select.add(new Option(label, String(i)));
    if (!i || units[i].section !== units[i - 1].section) {
      let j = i;
      while (j + 1 < units.length && units[j + 1].section === units[i].section) j++;
      section.add(new Option(units[i].section || label, i + "-" + j));
      if (last === undefined && i === first) last = j;
    }
  }
  setRange(first, last ?? units.length - 1, position);
}

byId("reference-prev").addEventListener("click", () => { firstVisible -= unitsPerView; draw(); });
byId("reference-next").addEventListener("click", () => { firstVisible += unitsPerView; draw(); });
section.addEventListener("input", () => {
  if (section.value) setRange(...section.value.split("-").map(Number));
});
start.addEventListener("input", () => setRange(+start.value, Math.max(+start.value, +end.value)));
end.addEventListener("input", () => setRange(Math.min(+start.value, +end.value), +end.value));
width.addEventListener("input", () => {
  revised();
  for (const unit of inRange()) {
    widths.set(unit, width.value);
  }
  draw();
});

let warnings = [];
function resetFile() {
  analysis?.abort();
  analysis = null;
  units = [];
  choices.clear();
  widths.clear();
  warnings = [];
  byId("reference-warnings").textContent = "";
  revised();
  ranges();
}
fileInput.addEventListener("change", resetFile);

function mergeReanalysis(old, replacement) {
  const remaining = [...old.candidates];
  for (const [i, fresh] of replacement.candidates.entries()) {
    const running = isRunning(fresh.role) && typeof fresh.source_group === "string";
    const sameSource = previous => previous.role === fresh.role && previous.key === fresh.key &&
      previous.source_group === fresh.source_group && previous.variant === fresh.variant;
    const sameReading = previous => previous.basis === fresh.basis && previous.unit === fresh.unit &&
      JSON.stringify(previous.value) === JSON.stringify(fresh.value) &&
      JSON.stringify(previous.options) === JSON.stringify(fresh.options);
    let index = remaining.findIndex(previous => sameSource(previous) && sameReading(previous) &&
      (running || previous.evidence === fresh.evidence));
    if (index < 0 && running) index = remaining.findIndex(sameSource);
    if (index < 0) continue;
    const [previous] = remaining.splice(index, 1);
    const choice = choiceFor(previous);
    if (running && !sameReading(previous)) {
      choice.retry = {candidate: fresh};
      replacement.candidates[i] = previous;
    } else {
      delete choice.retry;
      if (!choice.checked && !choice.faceChosen && choice.edited === undefined) choice.face = suggestedFace(fresh);
      if (running && previous.evidence && previous.evidence !== fresh.evidence) {
        (choice.previousEvidence ??= new Set()).add(previous.evidence);
      }
      choices.delete(previous);
      choices.set(fresh, choice);
    }
  }
  for (const previous of remaining) {
    if (isRunning(previous.role)) {
      choiceFor(previous).retry = {candidate: null};
      replacement.candidates.push(previous);
    } else choices.delete(previous);
  }
  if (widths.has(old)) widths.set(replacement, widths.get(old));
  widths.delete(old);
}

async function analyze(page) {
  const file = fileInput.files[0];
  if (!file || !config || analysis) return;
  revised();
  if (file.size > config.max_bytes) {
    message.textContent = `참고 파일은 ${config.max_bytes.toLocaleString()}바이트까지 분석할 수 있습니다.`;
    return;
  }
  const controller = new AbortController();
  analysis = controller;
  draw();
  message.textContent = "참고 파일 분석 중…";
  try {
    const query = new URLSearchParams({name: file.name});
    if (page !== undefined) query.set("page", String(page));
    const result = await jsonRequest("/api/reference/analyze?" + query, {
      method: "POST", body: file, signal: controller.signal,
    });
    if (analysis !== controller) return;
    if (page === undefined) {
      const previous = new Map(units.map(u => [u.page ?? u.location, u]));
      for (const unit of result.units) {
        const old = previous.get(unit.page ?? unit.location);
        if (old) mergeReanalysis(old, unit);
      }
      units = result.units;
      warnings = result.warnings;
      ranges();
    } else {
      const index = units.findIndex(unit => unit.page === page);
      const replacement = result.units.find(unit => unit.page === page);
      if (!replacement || index < 0) throw new Error("해당 쪽의 분석 결과를 받지 못했습니다.");
      mergeReanalysis(units[index], replacement);
      units[index] = replacement;
      warnings = [...new Set([...warnings.filter(w => !w.startsWith(page + "쪽")), ...result.warnings])];
      ranges(+start.value, +end.value, firstVisible);
    }
    byId("reference-warnings").textContent = warnings.join("\n");
    message.textContent = units.length ? "" : "적용할 서식 후보가 없습니다.";
  } catch (error) {
    if (analysis === controller) message.textContent = "분석 실패 · " + error.message;
  } finally {
    if (analysis === controller) { analysis = null; draw(); }
  }
}

byId("reference-analyze").addEventListener("click", () => analyze());

function typedValue(candidate, unit, choice) {
  const key = candidate.key;
  if (choice.retry) throw new Error("재분석 근거를 비교한 뒤 기존 선택을 유지하거나 재분석 값을 사용하세요.");
  if (candidate.options?.length && !choice.option) throw new Error("번호 해석에서 원문 숫자 또는 자동 쪽 번호를 선택하세요.");
  if (candidate.unit === "width_per_10000" && !(Number(calibratedWidth(unit)) > 0)) {
    throw new Error(unit.location + "의 실물 기준 폭을 mm로 입력하세요.");
  }
  const raw = proposed(candidate, unit, choice);
  if (key === "visible") return raw === true || raw === "true";
  if (key === "font" && !settings.fonts.some(font => font.id === raw)) {
    throw new Error("사용할 지원 글꼴을 선택하세요.");
  }
  if (documentKeys.includes(key)) {
    const values = raw.trim().split(/[,\s]+/);
    if (values.length !== (key === "page_mm" ? 2 : 4) || values.some(v => !v || !Number.isFinite(Number(v)))) {
      throw new Error(keyName(key) + "의 각 치수를 쉼표로 구분해 입력하세요.");
    }
    return values;
  }
  return raw;
}

function sameRuleValue(role, key, left, right) {
  if (JSON.stringify(left) === JSON.stringify(right)) return true;
  if (!isRunning(role) || (key !== "font_pt" && key !== "offset_mm")) return false;
  return sameDecimal(left, right);
}

function draftStyle(strict = true, suggestions) {
  const style = styleForm.get();
  const rules = new Map();
  const selected = new Map();
  const importedAreas = new Map();
  const chosenVisibility = {section_rules: []};
  let edited = false;
  function assign(path, role, face, key, value, override) {
    if (strict && !bound(path)) throw new Error("없거나 중복된 제목을 고유한 적용 대상에 다시 연결하세요.");
    const id = areaId(path, role, face);
    const rule = rules.get(id) ?? newRule(path, role, face, {});
    if (strict && !override && Object.hasOwn(rule.values, key) && !sameRuleValue(role, key, rule.values[key], value)) {
      throw new Error("저장된 구간 서식이 충돌합니다. 다시 연결할 대상을 바꾸거나 한 규칙을 제외하세요.");
    }
    rule.values[key] = value;
    rules.set(id, rule);
  }
  for (const {rule, path, remove} of bindings) {
    if (!strict && !isRunning(rule.role)) continue;
    if (remove || path !== JSON.stringify(rule.path)) edited = true;
    if (!remove) {
      for (const [key, value] of Object.entries(rule.values)) assign(path, rule.role, rule.face ?? "default", key, value, false);
    }
  }
  for (const [id, edit] of runningEdits) {
    for (const [key, value] of Object.entries(edit.values)) {
      edited = true;
      if (value !== null) assign(edit.path, edit.role, edit.face, key, value, true);
      else if (rules.has(id)) {
        const values = rules.get(id).values;
        delete values[key];
        if (!Object.keys(values).length) rules.delete(id);
      }
    }
  }
  const priorAreas = new Set(rules.keys());
  for (const unit of inRange()) {
    for (const candidate of unit.candidates) {
      const choice = choiceFor(candidate);
      if (!choice.checked) continue;
      const {key} = candidate;
      const {path, role, face} = choice;
      if (style.gutter_mm !== undefined && role === "document" && key === "margins_mm") continue;
      if (!strict && !isRunning(role)) continue;
      if (isRunning(role) && !Object.hasOwn(faces, face)) {
        if (strict) throw new Error(keyName(key) + " 후보의 적용할 면을 선택하세요.");
        continue;
      }
      let value = strict ? typedValue(candidate, unit, choice) : proposed(candidate, unit, choice);
      if (key === "visible") value = value === true || value === "true";
      const id = JSON.stringify([path, role, face, key]);
      if (strict && selected.has(id) && !sameRuleValue(role, key, selected.get(id), value)) {
        throw new Error(keyName(key) + " 후보가 충돌합니다. 같은 대상에는 사용할 값 하나만 선택하세요.");
      }
      selected.set(id, value);
      edited = true;
      if (role === "document") style[key] = value;
      else assign(path, role, face, key, value, true);
      if (isRunning(role)) {
        if (key === "visible") chosenVisibility.section_rules.push(newRule(path, role, face, {visible: value}));
        else importedAreas.set(areaId(path, role, face), {path, role, face});
      }
    }
  }
  // Importing into a hidden area proposes visibility. Direct text edits never change it.
  const layer = {default: 0, odd: 1, even: 1, first: 2};
  const parentsFirst = [...importedAreas.values()].sort((a, b) =>
    JSON.parse(a.path).length - JSON.parse(b.path).length || layer[a.face] - layer[b.face]);
  for (const {path, role, face} of parentsFirst) {
    const id = areaId(path, role, face);
    if (Object.hasOwn(runningEdits.get(id)?.values ?? {}, "visible") || selected.has(JSON.stringify([path, role, face, "visible"]))) continue;
    const beforeImport = [...rules].filter(([id, rule]) => priorAreas.has(id) || Object.hasOwn(rule.values, "visible"));
    const priorStyle = {section_rules: beforeImport.map(([, rule]) => rule)};
    const parts = JSON.parse(path);
    if (priorStyle.section_rules.some(rule => rule.face && overlapsParentHide(rule, parts, role, face)) ||
        chosenVisibility.section_rules.some(rule => overlapsParentHide(rule, parts, role, face))) continue;
    const needsShow = runningCases(priorStyle, path, role, face).every(({firstDepth, parities}) =>
      parities.every(parity => !currentValue(priorStyle, path, role, "visible", face, parity, firstDepth) &&
        inheritedValue(chosenVisibility, path, role, "visible", face, parity, firstDepth) !== false));
    if (needsShow) {
      assign(path, role, face, "visible", true, true);
      suggestions?.add(id);
    }
  }
  if (strict && !edited && area.hidden) throw new Error("시험할 속성을 선택하거나 저장된 구간 서식의 대상을 변경하세요.");
  if (rules.size) style.section_rules = [...rules.values()];
  else delete style.section_rules;
  return style;
}

function displayTrial(url) {
  const container = byId("reference-proof");
  const href = url + "#navpanes=0&view=FitH";
  if (inlinePdf) {
    const frame = node("iframe");
    frame.title = "시험 조판 미리보기";
    frame.src = href;
    container.append(frame);
  }
  const link = node("a", "시험 PDF 열기");
  link.href = href;
  link.target = "_blank";
  link.rel = "noopener";
  container.append(link);
}

async function trial() {
  revised();
  if (busy() || analysis || targetsFor !== sourceText()) return;
  const controller = new AbortController();
  const revision = version;
  attempt = controller;
  let style;
  try {
    style = draftStyle();
    limits();
    message.textContent = "선택한 서식으로 시험 조판 중…";
    const blob = await fetchPdf(style, controller.signal);
    if (attempt !== controller || revision !== version || busy()) return;
    proof = {url: URL.createObjectURL(blob), style, revision};
    displayTrial(proof.url);
    byId("reference-apply").disabled = false;
    message.textContent = "시험 PDF를 확인한 뒤 적용하세요.";
  } catch (error) {
    if (attempt !== controller) return;
    message.textContent = error.field ? error.field + " · " + error.message : error.message;
    const match = /^section_rules\[(\d+)\]\.values\.(\w+)$/.exec(error.field);
    const rule = match && style?.section_rules?.[Number(match[1])];
    if (rule && isRunning(rule.role) && runningKeys.includes(match[2])) {
      showArea(JSON.stringify(rule.path), rule.role, rule.face ?? "default");
      const field = byId("running-" + match[2]);
      byId("running-error").textContent = error.message;
      field.setAttribute("aria-invalid", "true");
      field.setAttribute("aria-describedby", "running-error");
      field.focus();
    }
  } finally {
    if (attempt === controller) attempt = null;
    limits();
  }
}

function closeReview() {
  const opener = byId(reviewOpener);
  panel.hidden = true;
  area.hidden = true;
  areaPath = globalPath;
  areaRole = "header";
  areaFace = "default";
  runningEdits.clear();
  byId("open-running").setAttribute("aria-expanded", "false");
  byId("open-reference").setAttribute("aria-expanded", "false");
  fileInput.value = "";
  resetFile();
  bindings = [];
  savedRules = "";
  opener.focus({preventScroll: true});
}

let reviewOpener = "open-reference";

export async function openReview(running = false) {
  reviewOpener = running ? "open-running" : "open-reference";
  if (!panel.hidden) {
    if (running) showArea(areaPath, areaRole);
    byId(running ? "running-title" : "reference-title").focus();
    return;
  }
  panel.hidden = false;
  byId("open-reference").setAttribute("aria-expanded", "true");
  if (running) showArea(areaPath, areaRole);
  byId(running ? "running-title" : "reference-title").focus();
  revised();
  refreshBindings();
  draw();
  readTargets(true);
  try {
    config = await jsonRequest("/api/reference/settings");
    byId("reference-model").textContent = config.model ? "스캔 지면 분석 모델 · " + config.model : "스캔 지면 분석 모델 미연결";
  } catch {
    config = undefined;
    byId("reference-model").textContent = "분석 설정을 읽지 못했습니다. 다시 열어 주세요.";
  }
  limits();
}

byId("reference-trial").addEventListener("click", trial);
byId("reference-targets").addEventListener("click", () => { revised(); readTargets(true); });
byId("reference-cancel").addEventListener("click", closeReview);
byId("reference-apply").addEventListener("click", () => {
  if (!proof || proof.revision !== version || busy()) return;
  const {url, style} = proof;
  proof = null; // The committed preview takes ownership of this blob URL.
  closeReview();
  pending = null;
  styleForm.set(style, {preserveMarginSplit: true});
  edited("style");
  showPdf(url, style);
});
