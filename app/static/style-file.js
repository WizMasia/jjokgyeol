"use strict";

async function requestStyle(action, body) {
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

bindFilePicker("open-style", "style-file", file =>
  openFile("style", () => requestStyle("open", file), styleForm.set, showStyleError)
);
byId("save-style").addEventListener("click", async () => {
  const values = styleForm.get();
  try {
    const result = await requestStyle("export", JSON.stringify(values));
    const blob = new Blob([JSON.stringify(result, null, 2) + "\n"], {type: "application/json"});
    saveBlob(blob, "쪽결-양식.json");
  } catch (error) {
    if (/^(gutter_mm|margins_mm(?:\[\d+\])?)$/.test(error.field)) {
      if (JSON.stringify(styleForm.get()) !== JSON.stringify(values)) return;
      marginError(error.field, error.message);
    }
    if (error.field === "article_expr") {
      if (!articleNumbering.checked || articleExpr.value !== values.article_expr) return;
      articleError(error.message);
    }
    showStyleError(error);
  }
});
