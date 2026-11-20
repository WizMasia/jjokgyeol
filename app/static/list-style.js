"use strict";

function listLevelEditor(settings, onEdit) {
  const panel = document.getElementById("list-style");
  const preset = document.getElementById("list-preset");
  const rows = document.getElementById("list-levels");
  const template = document.getElementById("list-level-row");
  const loadedRows = new WeakMap();
  const presets = settings.list_presets || {};
  const defaultPreset = settings.default_list_preset;
  const labels = {legal: "법령형", report: "보고서형"};
  const defaultLabel = labels[defaultPreset];
  preset.add(new Option(defaultLabel ? `기본값 (${defaultLabel})` : "기본값", ""));
  for (const key of Object.keys(presets)) preset.add(new Option(labels[key] || key, key));
  preset.add(new Option("사용자 지정", "custom"));

  function readRow(row) {
    const level = {...loadedRows.get(row)};
    for (const control of row.elements) {
      if (control.name) level[control.name] = control.value;
    }
    return level;
  }

  function numberRows() {
    Array.from(rows.children).forEach((row, index) => {
      row.querySelector("legend").textContent = `${index + 1}단계`;
      row.querySelector("button").disabled = rows.childElementCount === 1;
    });
  }

  function appendRow(level) {
    const row = template.content.firstElementChild.cloneNode(true);
    loadedRows.set(row, level);
    for (const control of row.querySelectorAll("input")) control.value = level[control.name];
    rows.append(row);
    return row;
  }

  function replaceRows(levels) {
    rows.replaceChildren();
    levels.forEach(appendRow);
    numberRows();
    panel.hidden = levels.length === 0;
  }

  preset.addEventListener("input", () => {
    if (preset.value !== "custom") replaceRows(presets[preset.value || defaultPreset] || []);
  });
  rows.addEventListener("input", () => { preset.value = "custom"; });
  rows.addEventListener("click", event => {
    const button = event.target.closest("button");
    if (!button || rows.childElementCount <= 1) return;
    const row = button.closest("fieldset");
    const neighbour = row.nextElementSibling || row.previousElementSibling;
    row.remove();
    numberRows();
    preset.value = "custom";
    neighbour.querySelector("input").focus();
    onEdit();
  });
  document.getElementById("add-level").addEventListener("click", () => {
    const row = appendRow(readRow(rows.lastElementChild));
    numberRows();
    preset.value = "custom";
    row.querySelector("input").focus();
    onEdit();
  });

  return {
    get() {
      if (preset.value) return Array.from(rows.children, readRow);
    },
    set(levels) {
      replaceRows(levels ?? presets[defaultPreset] ?? []);
      // An explicit choice of the default preset must survive saving and reopening.
      preset.value = levels === undefined ? "" : Object.keys(presets).find(key =>
        levels.length === presets[key].length && levels.every((row, i) =>
          Object.keys(row).length === Object.keys(presets[key][i]).length &&
          Object.entries(row).every(([field, value]) => String(value) === String(presets[key][i][field]))
        )
      ) || "custom";
    },
  };
}
