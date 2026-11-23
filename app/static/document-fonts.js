"use strict";

const documentFontReference = /^font-([a-f0-9]{64}|none)-([a-f0-9]{64}|none)$/;

function documentFontIds(style) {
  const ids = new Set([style.body_font, style.heading_font]);
  for (const row of [...Object.values(style.text_styles ?? {}), ...(style.list_levels ?? []),
    ...(style.section_rules ?? []).map(rule => rule.values)]) {
    if (row?.font) ids.add(row.font);
  }
  return [...ids].filter(id => typeof id === "string" && documentFontReference.test(id));
}

function documentFontEditor(settings) {
  const known = settings.font_assets;
  const builtins = known?.faces ?? {};
  const limits = settings.document_limits;
  byId("open-fonts").hidden = !known || !limits?.font_count;
  const weights = ["regular", "bold"];
  const weightLabel = {regular: "보통", bold: "굵게"};
  const builtinChoices = [...settings.fonts];
  const builtinBytes = new Map();
  const local = new Map(); // Only files the user chose on this device survive document replacement.
  let archived = new Map();
  const registered = new Set();
  let incomingChoices = [];
  const picker = byId("font-dialog");
  const families = byId("font-family");
  const message = byId("font-message");
  const evidence = byId("font-evidence");
  const fallback = byId("font-fallback");
  const devices = byId("device-fonts");
  const deviceReaders = new WeakMap();
  let turn = 0;
  let consent;
  const renderer = new URL("/api/render", location.href);
  const loopback = ["localhost", "localhost.", "[::1]"].includes(renderer.hostname) ||
    /^127(?:\.\d{1,3}){3}$/.test(renderer.hostname);

  function record(hash) {
    const entry = archived.get(hash);
    return entry?.file ? entry : local.get(hash) ?? entry ?? builtins[hash];
  }

  function slots(id) {
    const match = documentFontReference.exec(id);
    return match ? match.slice(1) : weights.map(weight =>
      Object.keys(builtins).find(hash => builtins[hash].family === id && builtins[hash].weight === weight));
  }

  function familyLabel(id) {
    const face = slots(id).filter(hash => hash && hash !== "none").map(record).find(f => f?.name);
    const name = face && [face.family || face.name, face.version].filter(Boolean).join(" · ");
    return name || incomingChoices.find(choice => choice.id === id)?.label ||
      "미연결 글꼴 " + slots(id).filter(hash => hash !== "none").map(hash => hash.slice(0, 8)).join(" · ");
  }

  function refreshChoices(style) {
    const ids = new Set([...registered, ...incomingChoices.map(choice => choice.id), ...documentFontIds(style)]);
    const choices = [...ids].map(id => ({id, label: familyLabel(id)}));
    settings.fonts = [...builtinChoices, ...choices];
    for (const select of document.querySelectorAll("select")) {
      if (select !== families && ![...select.options].some(opt => builtinChoices.some(font => font.id === opt.value))) continue;
      const previous = select.value;
      for (const option of [...select.options]) {
        if (documentFontReference.test(option.value)) option.remove();
      }
      if (select === families && !select.options.length) {
        for (const font of builtinChoices) select.add(new Option(font.label, font.id));
      }
      for (const font of choices) select.add(new Option(font.label, font.id));
      if (previous) select.value = previous;
    }
  }

  function cancel() {
    turn++;
    message.textContent = "";
    evidence.replaceChildren();
  }

  function currentOperation() {
    const ticket = ++turn;
    const revision = version;
    return () => ticket === turn && revision === version;
  }

  function builtin(hash) {
    if (!builtinBytes.has(hash)) {
      const reading = nativeDesktop ? desktop.builtinFont(hash) : fetch("/api/document-fonts/" + hash, {
        redirect: "error",
      }).then(response => {
        if (!response.ok) throw new Error("쪽결 기본 글꼴 또는 고지를 읽지 못했습니다.");
        return response.blob();
      });
      builtinBytes.set(hash, reading.catch(error => { builtinBytes.delete(hash); throw error; }));
    }
    return builtinBytes.get(hash);
  }

  function validateSizes(files) {
    if (files.length > limits.font_count || files.some(file => file.size > limits.font_file) ||
        files.reduce((n, file) => n + file.size, 0) > limits.font_total) {
      throw new Error("선택한 글꼴 파일의 수 또는 크기가 한도를 넘었습니다.");
    }
  }

  function section(title, lines, url) {
    const item = document.createElement("section");
    const heading = document.createElement("h3");
    heading.textContent = title;
    item.append(heading);
    for (const text of lines.filter(Boolean)) {
      const line = document.createElement("p");
      line.textContent = text;
      item.append(line);
    }
    if (url && /^https?:\/\//i.test(url)) {
      const link = document.createElement("a");
      link.href = url;
      link.textContent = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      item.append(link);
    }
    return item;
  }

  function rightsCard(face) {
    const status = (allowed, decision) => allowed ? "허용" : decision === "restricted" ? "제한" : "근거 부족";
    return section([face.name, face.version, weightLabel[face.weight]].filter(Boolean).join(" · "), [
      face.copyright, face.license, "PDF 내장 · " + status(face.pdf, face.status?.pdf),
      "편집 문서에 원본 포함 · " + status(face.archive, face.status?.archive),
      face.fsType != null ? "OpenType fsType · " + face.fsType : "", face.reason,
    ], face.url);
  }

  async function inspectFile(file) {
    if (!(file instanceof Blob)) throw new Error("글꼴 파일을 읽지 못했습니다.");
    validateSizes([file]);
    const rights = await FontRights.inspect(file, known);
    return {...rights, file};
  }

  async function showRights() {
    const current = currentOperation();
    const id = families.value;
    try {
      const cards = [];
      for (const [index, hash] of slots(id).entries()) {
        let face = record(hash);
        if (builtins[hash] && !face?.file) face = await inspectFile(await builtin(hash));
        if (!current()) return;
        cards.push(face?.file ? rightsCard(face) : section(weightLabel[weights[index]],
          ["원본 파일이 없어 쪽결 기본 글꼴로 조판합니다."]));
      }
      evidence.replaceChildren(...cards);
    } catch (error) {
      if (current()) message.textContent = error.message;
    }
  }

  async function readSelection(items) {
    const current = currentOperation();
    const inspected = new Map();
    const errors = [];
    try {
      if (items.length > limits.font_count) throw new Error("선택한 글꼴 수가 한도를 넘었습니다.");
      const files = [];
      for (const item of items) {
        if (!current()) return;
        let file;
        try { file = await item.read(); }
        catch (error) { errors.push(item.name + " · " + error.message); continue; }
        files.push(file);
        validateSizes(files);
        try {
          const face = await inspectFile(file);
          inspected.set(face.hash, face);
        } catch (error) { errors.push(item.name + " · " + error.message); }
      }
      if (!current()) return;
      const style = styleForm.get();
      const used = new Set(documentFontIds(style).flatMap(slots));
      let reconnected = false;
      const groups = new Map();
      for (const face of inspected.values()) {
        if (face.supported === false || !weights.includes(face.weight)) {
          errors.push((face.name || "글꼴 파일") + " · " + face.reason);
          continue;
        }
        if (used.has(face.hash) && !record(face.hash)?.file) reconnected = true;
        local.set(face.hash, face);
        const key = JSON.stringify([face.family || face.name, face.version]);
        const group = groups.get(key) ?? {};
        if (group[face.weight]) registered.add(pairId(group));
        group[face.weight] = face.hash;
        groups.set(key, group);
      }
      let latest;
      for (const group of groups.values()) {
        // A second selection can complete a previously chosen single-weight family.
        for (const hash of Object.values(group)) {
          const face = local.get(hash);
          for (const previous of local.values()) {
            if ((previous.family || previous.name) === (face.family || face.name) &&
                previous.version === face.version && !group[previous.weight]) group[previous.weight] = previous.hash;
          }
        }
        latest = pairId(group);
        registered.add(latest);
      }
      refreshChoices(style);
      if (latest) families.value = latest;
      if (reconnected) {
        styleRevision++;
        changed();
      }
      updateFallback(style);
      evidence.replaceChildren(...[...inspected.values()].map(rightsCard));
      message.textContent = errors.join("\n");
    } catch (error) {
      if (current()) message.textContent = error.message;
    }
  }

  function pairId(group) { return "font-" + (group.regular || "none") + "-" + (group.bold || "none"); }

  async function listDevice() {
    const current = currentOperation();
    try {
      if (!nativeDesktop && !window.queryLocalFonts) throw new Error();
      const list = nativeDesktop ? await desktop.listFonts() : await window.queryLocalFonts();
      if (!current()) return;
      devices.replaceChildren();
      const seen = new Set();
      for (const entry of list) {
        const key = entry.id ?? entry.postscriptName ?? entry.fullName;
        if (seen.has(key)) continue;
        seen.add(key);
        const name = entry.name || entry.fullName || key;
        const option = new Option(entry.reason ? name + " · " + entry.reason : name);
        option.disabled = Boolean(entry.reason);
        deviceReaders.set(option, {name: option.text, read: () => nativeDesktop ? desktop.readFont(entry.id) : entry.blob()});
        devices.add(option);
      }
      byId("device-font-list").hidden = false;
      message.textContent = list.length ? "" : "기기 글꼴을 찾지 못했습니다. 글꼴 파일을 열어 주세요.";
    } catch {
      if (current()) message.textContent = "기기 글꼴 목록에 접근할 수 없습니다. 글꼴 파일을 열어 주세요.";
    }
  }

  function resolution(style) {
    const files = new Map();
    const warnings = [];
    let missing = false;
    const appliedFonts = {};
    const defaultSlots = slots(settings.defaults.body_font);
    const applied = builtinChoices.find(font => font.id === settings.defaults.body_font)?.label ?? settings.defaults.body_font;
    for (const id of documentFontIds(style)) {
      const pair = slots(id);
      appliedFonts[id] = pair;
      for (const [i, hash] of pair.entries()) {
        if (builtins[hash]) continue;
        const face = record(hash);
        let reason = "";
        if (hash === "none") reason = "해당 굵기 미지정";
        else if (!face?.file) reason = "파일 미연결";
        else if (!face.pdf) reason = face.reason || "PDF 내장 권한 부족";
        else if (!nativeDesktop && !loopback && !face.remote) reason = "원격 서버 전송 권한 부족";
        else if (!nativeDesktop && consent === false) reason = "조판기 전송 거절";
        if (reason) {
          pair[i] = defaultSlots[i] ?? null;
          warnings.push(familyLabel(id) + " " + weightLabel[weights[i]] + " → " + applied + " (" + reason + ")");
          if (hash !== "none") missing = true;
        } else files.set(hash, face.file);
      }
    }
    return {files, warnings, missing, appliedFonts: Object.keys(appliedFonts).length ? appliedFonts : undefined};
  }

  function updateFallback(style) {
    const state = resolution(style);
    fallback.textContent = state.warnings.join(" · ");
    return state;
  }

  function resolveForRender(style) {
    let state = resolution(style);
    if (state.files.size && !nativeDesktop && consent === undefined) {
      consent = window.confirm(renderer.href + " 조판기로 선택한 글꼴 파일을 보내 PDF를 만듭니다. 이번 세션 동안 전송을 허용할까요?");
      state = resolution(style);
    }
    fallback.textContent = state.warnings.join(" · ");
    if (state.files.size) validateSizes([...state.files.values()]);
    return state;
  }

  function frame(request, files) {
    const faces = [...files].map(([hash, file]) => ({hash, size: file.size}));
    const header = new TextEncoder().encode(JSON.stringify({request, faces}));
    const length = new DataView(new ArrayBuffer(4));
    length.setUint32(0, header.length, false);
    return new Blob([length, header, ...files.values()], {type: "application/x-jjok-font-render"});
  }

  async function archiveFonts(style) {
    // Freeze every reference and blob before the first await. Both fallback families take part in glyph fallback.
    const selected = new Map(Object.entries(builtins).filter(([, data]) => data.fallback !== false)
      .map(([hash, data]) => [hash, {...archived.get(hash), ...data, hash}]));
    for (const id of documentFontIds(style)) {
      for (const [i, hash] of slots(id).entries()) {
        if (hash !== "none" && !selected.has(hash)) selected.set(hash, record(hash) ?? {
          hash, weight: weights[i], name: familyLabel(id), version: "",
        });
      }
    }
    const result = [];
    for (const [hash, face] of selected) {
      const {name, version, weight} = face;
      const entry = {hash, name, version, weight};
      if (builtins[hash]) {
        entry.file = face.file ?? await builtin(hash);
        entry.notice = face.notice ?? await builtin(known.notice);
      } else if (face.file && face.archive) {
        entry.file = face.file;
        entry.notice = await FontRights.notice(face);
      }
      result.push(entry);
    }
    return result;
  }

  async function readArchive(fonts) {
    const result = new Map();
    for (const face of fonts ?? []) {
      if (face.file) {
        const inspected = await inspectFile(face.file);
        if (inspected.hash !== face.hash) throw new Error("글꼴 파일의 해시가 다릅니다.");
        result.set(face.hash, {...inspected, notice: face.notice});
      } else result.set(face.hash, face);
    }
    return result;
  }

  byId("open-fonts").addEventListener("click", () => {
    cancel();
    refreshChoices(styleForm.get());
    families.value = byId("body_font").value;
    picker.showModal();
  });
  byId("close-fonts").addEventListener("click", () => { cancel(); picker.close(); });
  picker.addEventListener("cancel", cancel);
  byId("open-font-files").addEventListener("click", () => byId("font-files").click());
  byId("font-files").addEventListener("change", event => {
    const files = [...event.target.files];
    event.target.value = "";
    readSelection(files.map(file => ({name: file.name, read: () => file})));
  });
  byId("list-device-fonts").addEventListener("click", listDevice);
  byId("use-device-fonts").addEventListener("click", () => readSelection([...devices.selectedOptions].map(opt => deviceReaders.get(opt))));
  byId("inspect-font").addEventListener("click", showRights);
  families.addEventListener("change", showRights);
  byId("apply-font").addEventListener("click", () => {
    cancel();
    const control = byId("body_font");
    if (families.value && control.value !== families.value) {
      control.value = families.value;
      control.dispatchEvent(new Event("input", {bubbles: true}));
    }
    picker.close();
  });
  return {refreshChoices, cancel, resolveForRender, updateFallback, frame, archiveFonts, readArchive,
    restore(records) { archived = records; },
    receiveChoices(choices) { incomingChoices = choices ?? []; },
    choices: () => settings.fonts.filter(font => documentFontReference.test(font.id)),
  };
}
