"use strict";

const alignmentChoices = [["왼쪽", "left"], ["가운데", "center"], ["오른쪽", "right"]];

function typographyEditor(settings) {
  const fieldset = document.getElementById("text-roles");
  const role = document.getElementById("text-role");
  const controls = [...fieldset.querySelectorAll("[data-text]")];
  const spacingPanel = fieldset.querySelector(".spacing-fields");
  const spacingFields = paragraphSpacingFields(spacingPanel, "기본");
  const enabled = document.getElementById("tracking-enabled");
  const layout = document.getElementById("paragraph-layout");
  layout.add(new Option("이전 양식", ""));
  layout.add(new Option("문단 전체 배치 비교", "balanced"));
  layout.add(new Option("양끝맞춤", "justified"));
  const bounds = ["tracking-lower", "tracking-upper"].map(id => document.getElementById(id));
  const spaces = [...document.querySelectorAll('#style [name="space_pct"]')];
  const notice = document.getElementById("spacing-notice");
  let spacingEdited = false;
  const base = document.getElementById("ko_tracking_pct");
  const errorText = document.getElementById("typography-error");
  const defaults = settings.text_defaults;
  let styles = {};
  fieldset.hidden = document.getElementById("tracking-limits").hidden = !defaults;

  role.add(new Option("0단계 본문", "body"));
  for (let level = 1; level <= 6; level++) role.add(new Option(level + "단계 제목", "heading" + level));
  role.add(new Option("주", "note"));
  role.add(new Option("단어 풀이", "gloss"));
  const bodyFields = {font: document.getElementById("body_font"), font_pt: document.getElementById("body_pt")};
  const options = {
    align: alignmentChoices,
    frame: [["테두리", "box"]],
    font: settings.fonts.map(font => [font.label, font.id]),
    weight: [["보통", "normal"], ["굵게", "bold"]],
    slant: [["바로", "normal"], ["기울임", "italic"]],
  };

  function clearError() {
    for (const control of [...controls, ...spacingFields.map(field => field.input), ...bounds, base, layout]) {
      if (control.getAttribute("aria-describedby") === errorText.id) {
        control.removeAttribute("aria-invalid");
        control.removeAttribute("aria-describedby");
      }
    }
    errorText.textContent = "";
  }

  function show() {
    if (!defaults) return;
    const active = role.value;
    const heading = active.startsWith("heading");
    spacingPanel.hidden = active !== "body" && !heading;
    for (const field of spacingFields) field.set(styles[active]?.[field.key]);
    const body = bodyFields.font_pt.value;
    const defaultSize = heading ? Number(body) * settings.heading_per_10000[Number(active.slice(7)) - 1] / 10000
      : active === "note" ? defaults.note_pt : Number(body) * defaults.gloss_per_10000 / 10000;
    const fallback = {
      font: document.getElementById(heading ? "heading_font" : "body_font").value,
      color: defaults.color, font_pt: defaultSize, slant: defaults.slant,
      weight: heading ? defaults.heading_weight : defaults.weight,
    };
    for (const control of controls) {
      const key = control.dataset.text;
      const bodyField = active === "body" && bodyFields[key];
      control.closest("label").hidden = (key === "align" && active !== "body" && !heading) ||
        (key === "frame" && active !== "heading1");
      if (control.tagName === "SELECT") {
        const label = [...control.options].slice(1).find(option => option.value === fallback[key])?.textContent;
        control.options[0].hidden = control.options[0].disabled = Boolean(bodyField);
        control.options[0].textContent = !bodyField && label ? "기본 · " + label : "기본";
      } else {
        control.placeholder = bodyField ? "" : key === "font_pt" ? "기본 · " + fallback[key] : String(fallback[key]);
      }
      control.value = bodyField ? bodyField.value : styles[active]?.[key] ?? "";
    }
  }

  for (const control of controls) {
    const key = control.dataset.text;
    if (control.tagName === "SELECT") {
      control.add(new Option("기본", ""));
      for (const [label, value] of options[key]) control.add(new Option(label, value));
    }
    function editControl(event) {
      if (role.value === "body" && bodyFields[key]) {
        event.stopPropagation();
        bodyFields[key].value = control.value;
        bodyFields[key].dispatchEvent(new Event(event.type, {bubbles: true}));
        return;
      }
      if (event.type !== "input") return;
      const row = styles[role.value] ??= {};
      if (control.value) row[key] = control.value;
      else delete row[key];
      if (!Object.keys(row).length) delete styles[role.value];
    }
    control.addEventListener("input", editControl);
    if (bodyFields[key]) control.addEventListener("change", editControl);
  }

  spacingPanel.addEventListener("input", event => {
    const field = spacingFields.find(field => field.mode === event.target || field.input === event.target);
    const row = styles[role.value] ??= {};
    field.writeTo(row);
    if (!Object.keys(row).length) delete styles[role.value];
  });

  role.addEventListener("input", event => {
    event.stopPropagation();
    clearError();
    show();
  });
  for (const id of ["body_pt", "body_font", "heading_font"]) {
    document.getElementById(id).addEventListener("input", show);
  }
  function setBounds(values) {
    enabled.checked = values !== undefined;
    layout.options[0].textContent = enabled.checked ? "이전 양식 · 자간 범위로 배치 비교" : "이전 양식";
    bounds.forEach((control, i) => {
      control.disabled = !enabled.checked;
      control.value = values?.[i] ?? base.value;
    });
  }
  function manualSpacing() {
    spacingEdited = true;
    notice.textContent = "";
  }
  spaces.forEach(control => control.addEventListener("input", manualSpacing));
  layout.addEventListener("input", () => {
    clearError();
    notice.textContent = "";
    const suggested = settings.justified_spacing ?? settings.new_spacing;
    if (layout.value !== "justified" || !suggested?.ko_tracking_bounds_pct || enabled.checked || spacingEdited ||
        !spaces.every(control => sameDecimal(control.value, "100"))) return;
    const [low, high] = suggested.ko_tracking_bounds_pct;
    enabled.closest("details").open = true;
    if (!(compareDecimal(base.value, low) >= 0 && compareDecimal(base.value, high) <= 0)) {
      notice.textContent = `현재 기본 자간을 유지했습니다. 줄별 보정을 쓰려면 자간 한계를 직접 지정하세요. 권장 범위는 ${low}% ~ ${high}%입니다.`;
      return;
    }
    setBounds(suggested.ko_tracking_bounds_pct);
    spaces.forEach((control, i) => { control.value = suggested.space_pct[i]; });
  });
  enabled.addEventListener("input", () => {
    manualSpacing();
    setBounds(enabled.checked ? [base.value, base.value] : undefined);
  });
  base.addEventListener("input", () => {
    notice.textContent = "";
    if (!enabled.checked) setBounds(undefined);
  });

  return {
    clearError,
    get() {
      const values = {};
      if (layout.value) values.paragraph_layout = layout.value;
      if (Object.keys(styles).length) values.text_styles = structuredClone(styles);
      if (enabled.checked) values.ko_tracking_bounds_pct = bounds.map(control => control.value);
      return values;
    },
    set(values, preserveDraft) {
      clearError();
      notice.textContent = "";
      if (!preserveDraft) spacingEdited = false;
      layout.value = values.paragraph_layout ?? "";
      styles = structuredClone(values.text_styles ?? {});
      setBounds(values.ko_tracking_bounds_pct);
      show();
    },
    error(field, message) {
      clearError();
      let invalid = [];
      if (field === "paragraph_layout") invalid = [layout];
      if (field === "ko_tracking_pct") invalid = [base];
      if (field === "ko_tracking_bounds_pct") invalid = bounds;
      if (field?.startsWith("text_styles.")) {
        const [, target, key] = field.split(".");
        role.value = target;
        show();
        invalid = controls.filter(control => control.dataset.text === key);
        const field = spacingFields.find(field => field.key === key);
        if (field) invalid.push(field.input);
      }
      if (invalid.length) errorText.textContent = message;
      for (const control of invalid) {
        control.setAttribute("aria-invalid", "true");
        control.setAttribute("aria-describedby", errorText.id);
      }
    },
  };
}
