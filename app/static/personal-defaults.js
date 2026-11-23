"use strict";

const personalDefaultsKey = "jjokgyeol.personal-defaults.v1";
const defaultsStatus = document.getElementById("personal-defaults-status");
const startupStyleToken = Symbol("personal defaults");
let defaultsWrite = 0;

function openSettingsConnection(connection) {
  connectionForm.open(connection);
  if (!connection?.base_url) return;
  try {
    const saved = JSON.parse(localStorage.getItem(personalDefaultsKey));
    if (saved?.connection?.provider === connection.provider && saved.connection.base_url === connection.base_url &&
        Array.isArray(saved.model_ids) && saved.model_ids.every(id => typeof id === "string")) {
      connectionForm.setManualIds(saved.model_ids);
    }
  } catch {
    defaultsStatus.textContent = "개인 모델 목록을 불러오지 못했습니다.";
  }
}

function publicDefaultConnection(connection) {
  const {provider, base_url, model, api_key} = connection;
  if (provider === "operator") return {provider: !nativeDesktop && settings.operator_decision ? "operator" : "rule"};
  if (provider === "rule" || (provider === "builtin" && settings.builtin_decision)) return {provider};
  if (!settings.decision_providers?.some(item => item.id === provider) ||
      typeof model !== "string" || !model.trim() || /[\x00-\x1f\x7f]/.test(model)) {
    throw new Error(decisionMessages.invalid_connection);
  }
  if (provider === "gemini" && !/^(?:models\/)?[a-zA-Z0-9_-][a-zA-Z0-9._-]*$/.test(model)) {
    throw new Error(decisionMessages.model);
  }

  // Inspect the original components before URL normalizes dot segments or discards delimiters.
  const parts = typeof base_url === "string" && /^https?:\/\/([^/]+)(\/.*)?$/i.exec(base_url);
  if (!parts || /[\s?#\\\x00-\x1f\x7f]/.test(base_url) || parts[1].includes("@")) throw new Error(decisionMessages.address);
  try {
    const url = new URL(base_url);
    const path = (parts[2] ?? "").split("/").map(decodeURIComponent);
    if (!url.hostname || url.port === "0" || parts[2]?.startsWith("//") ||
        path.some(part => part === "." || part === ".." || /[\/\\%?#\x00-\x1f\x7f]/.test(part)) ||
        (api_key && (decodeURIComponent(base_url).includes(api_key) || model.includes(api_key)))) throw new Error();
  } catch {
    throw new Error(decisionMessages.address);
  }
  return {provider, base_url, model};
}

async function savePersonalDefaults() {
  const turn = ++defaultsWrite;
  defaultsStatus.textContent = "";
  try {
    if (stylePanel.inert || fileLoads.style || !connectionForm.valid()) throw new Error(decisionMessages.invalid_connection);
    const draft = connectionForm.value();
    const connection = publicDefaultConnection(draft);
    const ids = connection.base_url ? connectionForm.manualIds() : [];
    if (draft.api_key && ids.some(id => id.includes(draft.api_key))) throw new Error(decisionMessages.invalid_connection);
    const style = await requestStyle("export", JSON.stringify(styleForm.get()));
    if (turn !== defaultsWrite) return;
    localStorage.setItem(personalDefaultsKey, JSON.stringify({style, connection, ...(ids.length && {model_ids: ids})}));
    defaultsStatus.textContent = "새 문서 기본값을 저장했습니다.";
  } catch (error) {
    if (turn !== defaultsWrite) return;
    if (error.field) showStyleFieldError(error.field, error.message);
    defaultsStatus.textContent = "새 문서 기본값을 저장하지 못했습니다. " +
      (error.field || [decisionMessages.address, decisionMessages.invalid_connection, decisionMessages.model].includes(error.message)
        ? error.message : "양식과 저장 공간을 확인하세요.");
  }
}

function restorePersonalDefaults() {
  defaultsWrite++;
  try {
    localStorage.removeItem(personalDefaultsKey);
    defaultsStatus.textContent = "다음 새 문서에 사용할 쪽결 기본값을 복원했습니다.";
  } catch {
    defaultsStatus.textContent = "쪽결 기본값을 복원하지 못했습니다. 저장 공간을 확인하세요.";
  }
}

async function loadPersonalDefaults() {
  let turn;
  try {
    const raw = localStorage.getItem(personalDefaultsKey);
    if (raw === null) return;
    const saved = JSON.parse(raw);
    const connection = publicDefaultConnection(saved.connection);
    const revision = connectionRevision;
    turn = beginFileOpen("style", startupStyleToken);
    // The existing style-file converter is installed by the next deferred script.
    await new Promise(resolve => document.addEventListener("DOMContentLoaded", resolve, {once: true}));
    if (fileLoads.style !== turn) return;
    const style = await requestStyle("open", JSON.stringify(saved.style));
    if (fileLoads.style !== turn) return;
    styleForm.set(style);
    styleRevision++;
    if (revision === connectionRevision) {
      applyConnection(connection);
      if (settingsDialog.open && !settingsDialog.classList.contains("decision-edited")) openSettingsConnection(connection);
    }
  } catch {
    if (!turn || fileLoads.style === turn) defaultsStatus.textContent = "개인 기본값을 불러오지 못했습니다. 쪽결 기본값으로 시작합니다.";
  } finally {
    if (turn) finishFileOpen("style", turn);
  }
}

function initializePersonalDefaults() {
  byId("save-personal-defaults").addEventListener("click", savePersonalDefaults);
  byId("restore-personal-defaults").addEventListener("click", restorePersonalDefaults);
  if (!settingsView) {
    if (source.value) edited("source");
    else openMarkdown(null);
    return loadPersonalDefaults();
  }
}
