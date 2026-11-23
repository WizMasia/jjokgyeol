"use strict";

// Style rows keep decimal drafts as text until the shared Style converter validates them.
function paragraphSpacingFields(container, inheritedLabel) {
  const template = document.getElementById("spacing-field");
  return [["space_before_pt", "문단 위"], ["space_after_pt", "문단 아래"]].map(([key, label]) => {
    const group = template.content.firstElementChild.cloneNode(true);
    const mode = group.querySelector("select");
    const input = group.querySelector("input");
    const measure = input.closest("label");
    group.querySelector("label").prepend(label + " ");
    mode.dataset.spacingMode = input.dataset.spacingValue = key;
    mode.add(new Option(inheritedLabel, ""));
    mode.add(new Option("AI 자동", "auto"));
    mode.add(new Option("직접 입력", "manual"));
    input.setAttribute("aria-label", label + " 여백 (pt)");
    mode.addEventListener("input", () => {
      measure.hidden = mode.value !== "manual";
      input.removeAttribute("aria-invalid");
    });
    container.append(group);
    return {
      key, mode, input,
      set(value) {
        const manual = value != null && value !== "auto";
        mode.value = manual ? "manual" : value ?? "";
        input.value = manual ? value : "";
        measure.hidden = !manual;
        input.removeAttribute("aria-invalid");
      },
      writeTo(row) {
        if (mode.value === "manual") row[key] = input.value === "auto" ? "" : input.value;
        else if (mode.value === "auto") row[key] = "auto";
        else delete row[key];
      },
    };
  });
}
