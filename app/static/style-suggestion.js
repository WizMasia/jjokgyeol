const form = byId("suggestion-form");
const prompt = byId("suggestion-request");
const message = byId("suggestion-message");
const fields = byId("suggestion-fields");
const confirm = byId("suggestion-apply");
const cancel = byId("suggestion-cancel");
const submit = form.querySelector("button");
let active;

const labels = {
  body_pt: "본문 크기 (pt)", leading_pt: "행간 (pt)", body_font: "본문 글꼴", heading_font: "제목 글꼴",
  page_mm: "판형 폭·높이 (mm)", margins_mm: "여백 위·오른쪽·아래·왼쪽 (mm)", gutter_mm: "제본 여백 (mm)",
  first_indent_mm: "첫 줄 들여쓰기 (mm)", rest_indent_mm: "이후 줄 들여쓰기 (mm)",
  ko_tracking_pct: "한글 자간 (%)", space_pct: "어절 공백 최소·최대 (%)", article_expr: "조 번호 표현식",
  left: "왼쪽", center: "가운데", right: "오른쪽", visible: "표시 여부",
  expr: "표지 표현식", indent_mm: "들여쓰기 (mm)", gap_mm: "표지 뒤 간격 (mm)",
};

function display(value) {
  if (value === true) return "표시";
  if (value === false) return "숨김";
  if (value === undefined || value === "") return "비어 있음";
  if (Array.isArray(value)) return value.join(" · ");
  return settings.fonts.find(font => font.id === value)?.label ?? String(value);
}

function row(label, before, after) {
  const item = document.createElement("li");
  item.textContent = `${label}　${display(before)} → ${display(after)}`;
  fields.append(item);
}

function commonRules(rules, role) {
  const rows = (rules ?? []).filter(r => !r.path.length && r.role === role && (r.face ?? "default") === "default");
  return Object.assign({}, ...rows.map(r => r.values));
}

function showFields(before, patch) {
  for (const [key, value] of Object.entries(patch)) {
    if (key === "section_rules") {
      for (const [role, name] of [["header", "머리글"], ["footer", "바닥글"]]) {
        const old = commonRules(before.section_rules, role);
        const next = commonRules(value, role);
        for (const field of ["left", "center", "right", "visible"]) {
          if (field in next && old[field] !== next[field]) {
            row(`${name} ${labels[field]}`, old[field] ?? settings.running_defaults[field], next[field]);
          }
        }
      }
    } else if (key === "list_levels") {
      const old = before.list_levels ?? settings.list_presets[settings.default_list_preset];
      value.forEach((level, i) => {
        for (const [field, v] of Object.entries(level)) {
          if (old[i][field] !== v) row(`목록 ${i + 1}단계 ${labels[field]}`, old[i][field], v);
        }
      });
    } else {
      row(labels[key], before[key], value);
    }
  }
}

function clear(text) {
  active?.controller.abort();
  active = undefined;
  fields.replaceChildren();
  fields.hidden = confirm.hidden = cancel.hidden = true;
  submit.disabled = false;
  message.textContent = text;
}

const changedMessage = "현재 양식이 바뀌었습니다. 다시 요청하세요.";
function current(job) {
  if (active !== job) return false;
  if (fileLoads.style || JSON.stringify(styleForm.get()) !== JSON.stringify(job.before)) {
    clear(changedMessage);
    return false;
  }
  if (job.patch?.section_rules && !document.dispatchEvent(new Event("style-suggestion-check-rules", {cancelable: true}))) {
    clear("참고 서식 변경을 적용하거나 취소한 뒤 다시 요청하세요.");
    return false;
  }
  return true;
}

byId("open-suggestion").addEventListener("click", event => {
  const panel = byId("style-suggestion");
  panel.hidden = !panel.hidden;
  event.currentTarget.setAttribute("aria-expanded", String(!panel.hidden));
  if (panel.hidden) clear("");
  else prompt.focus();
});
document.addEventListener("style-suggestion-invalidate", () => { if (active) clear(changedMessage); });
byId("reference-bindings").addEventListener("input", () => { if (active) clear(changedMessage); });
prompt.addEventListener("input", () => clear(""));
cancel.addEventListener("click", () => clear("제안을 취소했습니다."));

form.addEventListener("submit", async event => {
  event.preventDefault();
  clear("");
  if (fileLoads.style) {
    message.textContent = "양식을 연 뒤 다시 요청하세요.";
    return;
  }
  const job = {before: styleForm.get(), controller: new AbortController()};
  active = job;
  submit.disabled = true;
  cancel.hidden = false;
  message.textContent = "양식 제안을 기다리는 중입니다.";
  try {
    const response = await fetch("/api/style-suggestion", {
      method: "POST", redirect: "error", signal: job.controller.signal,
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({request: prompt.value, style: job.before}),
    });
    const result = await response.json();
    job.patch = result.patch;
    if (!current(job)) return;
    if (!response.ok) throw new Error(result.message || "양식 제안을 받지 못했습니다.");
    if (!Object.keys(job.patch).length) {
      clear("변경할 항목이 없습니다.");
      return;
    }
    showFields(job.before, job.patch);
    fields.hidden = confirm.hidden = false;
    message.textContent = "제안한 값을 확인한 뒤 적용하세요.";
  } catch (error) {
    if (active === job) clear(error.message);
  }
});

confirm.addEventListener("click", () => {
  const job = active;
  if (!job?.patch || !current(job)) return;
  clear("제안을 적용했습니다.");
  styleForm.set({...job.before, ...job.patch}, {preserveDraft: true, inactiveArticleExpr: articleExpr.value});
  edited("style");
});
