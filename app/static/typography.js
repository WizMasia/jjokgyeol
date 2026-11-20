"use strict";

function typographyEditor(settings) {
  const fieldset = document.getElementById("text-roles");
  const role = document.getElementById("text-role");
  const controls = [...fieldset.querySelectorAll("[data-text]")];
  const enabled = document.getElementById("tracking-enabled");
  const layout = document.getElementById("paragraph-layout");
  layout.add(new Option("이전 양식", ""));
  layout.add(new Option("문단 전체 배치 비교", "balanced"));
  layout.add(new Option("양끝맞춤", "justified"));
  const bounds = ["tracking-lower", "tracking-upper"].map(id => document.getElementById(id));
  const base = document.getElementById("ko_tracking_pct");
  const errorText = document.getElementById("typography-error");
  const defaults = settings.text_defaults;
  let styles = {};
  fieldset.hidden = document.getElementById("tracking-limits").hidden = !defaults;

  role.add(new Option("본문", "body"));
  for (let level = 1; level <= 6; level++) role.add(new Option(level + "단계 제목", "heading" + level));
  role.add(new Option("주", "note"));
  role.add(new Option("단어 풀이", "gloss"));
  const options = {
    font: settings.fonts.map(font => [font.label, font.id]),
    weight: [["보통", "normal"], ["굵게", "bold"]],
    slant: [["바로", "normal"], ["기울임", "italic"]],
  };

  function clearError() {
    for (const control of [...controls, ...bounds, base, layout]) {
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
    const body = document.getElementById("body_pt").value;
    const defaultSize = heading ? Number(body) * settings.heading_per_10000[Number(active.slice(7)) - 1] / 10000
      : active === "note" ? defaults.note_pt : Number(body) * defaults.gloss_per_10000 / 10000;
    const fallback = {
      font: document.getElementById(heading ? "heading_font" : "body_font").value,
      color: defaults.color, font_pt: defaultSize, slant: defaults.slant,
      weight: heading ? defaults.heading_weight : defaults.weight,
    };
    for (const control of controls) {
      const key = control.dataset.text;
      control.parentElement.hidden = active === "body" && (key === "font" || key === "font_pt");
      if (control.tagName === "SELECT") {
        const label = options[key].find(pair => pair[1] === fallback[key])?.[0];
        control.options[0].textContent = label ? "기본 · " + label : "기본";
      } else {
        control.placeholder = String(fallback[key]);
      }
      control.value = styles[active]?.[key] ?? "";
    }
  }

  for (const control of controls) {
    const key = control.dataset.text;
    if (control.tagName === "SELECT") {
      control.add(new Option("기본", ""));
      for (const [label, value] of options[key]) control.add(new Option(label, value));
    }
    control.addEventListener("input", () => {
      const row = styles[role.value] ??= {};
      if (control.value) row[key] = control.value;
      else delete row[key];
      if (!Object.keys(row).length) delete styles[role.value];
    });
  }

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
  layout.addEventListener("input", clearError);
  enabled.addEventListener("input", () => {
    setBounds(enabled.checked ? [base.value, base.value] : undefined);
  });
  base.addEventListener("input", () => {
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
    set(values) {
      clearError();
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
      }
      if (invalid.length) errorText.textContent = message;
      for (const control of invalid) {
        control.setAttribute("aria-invalid", "true");
        control.setAttribute("aria-describedby", errorText.id);
      }
    },
  };
}
