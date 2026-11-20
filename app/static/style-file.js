"use strict";

async function requestStyle(action, body) {
  if (nativeDesktop) return desktop.style(action, body);
  const response = await fetch("/api/style/" + action, {
    method: "POST", redirect: "error", headers: {"Content-Type": "application/json"}, body,
  });
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.message || "양식을 확인하세요.");
    error.field = result.field;
    error.status = response.status;
    throw error;
  }
  return result;
}

function showStyleError(error) {
  errorText.textContent = error.message || "양식을 처리할 수 없습니다.";
}

bindFilePicker("open-style", "style-file", async file => {
  if (!await confirmStyleReplacement()) return;
  return openFile("style", () => requestStyle("open", file), values => {
    styleForm.set(values);
    styleRevision++;
    markDirty("style", false);
  }, showStyleError);
});
byId("save-style").addEventListener("click", async () => {
  const values = styleForm.get();
  const revision = styleRevision;
  try {
    const result = await requestStyle("export", JSON.stringify(values));
    const blob = new Blob([JSON.stringify(result, null, 2) + "\n"], {type: "application/json"});
    if (await saveBlob(blob, "쪽결-양식.json") && revision === styleRevision) markDirty("style", false);
  } catch (error) {
    if (JSON.stringify(styleForm.get()) !== JSON.stringify(values)) return;
    showStyleFieldError(error.field, error.message);
    showStyleError(error);
  }
});
