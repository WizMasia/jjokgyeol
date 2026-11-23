"use strict";

const decisionMessages = {
  invalid_connection: "연결 설정을 확인하세요.",
  address: "허용된 서버 주소를 확인하세요.",
  authentication: "인증에 실패했습니다. API 키를 확인하세요.",
  model: "설정에서 모델 ID를 확인하세요.",
  rate_limit: "모델 서버의 요청 한도에 도달했습니다. 잠시 뒤 다시 시도하세요.",
  timeout: "응답 시간 초과",
  builtin_unavailable: "이 버전에서 내장 모델을 불러올 수 없습니다.",
  unavailable: "모델 서버에 연결할 수 없습니다.",
  invalid_response: "모델이 유효한 후보를 반환하지 않았습니다.",
};

function decisionMessage(code) {
  return Object.hasOwn(decisionMessages, code) ? decisionMessages[code] : "";
}

function decisionSettings(providers, builtinSupported, operatorDefault) {
  const panel = byId("decision-settings");
  const mode = byId("decision-mode");
  const provider = byId("decision-provider");
  const fields = byId("decision-fields");
  const model = byId("decision-model");
  const address = byId("decision-base-url");
  const key = byId("decision-api-key");
  const keyHelp = byId("decision-key-help");
  const clearKey = byId("decision-clear-key");
  const loopback = ["localhost", "localhost.", "[::1]"].includes(location.hostname) ||
    /^127(?:\.\d{1,3}){3}$/.test(location.hostname);
  const keyAllowed = nativeDesktop || location.protocol === "https:" || (location.protocol === "http:" && loopback);
  key.disabled = clearKey.disabled = !keyAllowed;
  keyHelp.hidden = keyAllowed;
  if (!keyAllowed) key.setAttribute("aria-describedby", keyHelp.id);
  const check = byId("decision-check");
  const status = byId("decision-check-status");
  let generation = 0;
  let useDefaultConnection = true;
  if (operatorDefault) mode.add(new Option("운영자 설정", "default"));
  mode.add(new Option("규칙", "rule"));
  if (builtinSupported) mode.add(new Option("내장 모델", "builtin"));
  if (providers.length) mode.add(new Option("외부 API", "model"));
  for (const item of providers) provider.add(new Option(item.label, item.id));
  panel.hidden = !builtinSupported && !providers.length;

  const defaultAddress = () => providers[provider.selectedIndex]?.base_url || "";
  const checkRow = byId("decision-check-row");
  const checkHelp = byId("decision-check-cost");
  const externalHelp = checkHelp.textContent;
  function showMode() {
    const builtin = mode.value === "builtin";
    fields.hidden = mode.value !== "model";
    checkRow.hidden = fields.hidden && !builtin;
    check.textContent = builtin ? "내장 모델 확인" : "연결 확인";
    checkHelp.textContent = builtin ? "조판 작업자에서 예제 후보로 내장 모델을 확인합니다." : externalHelp;
  }
  function invalidateCheck() {
    generation++;
    status.textContent = "";
    check.disabled = false;
  }
  function changedDraft() {
    useDefaultConnection = false;
    settingsDialog.classList.add("decision-edited");
    invalidateCheck();
  }
  panel.addEventListener("input", event => {
    if (event.target === provider) address.value = defaultAddress();
    if (event.target === address || event.target === provider) key.value = "";
    if (event.target === mode && mode.value === "builtin") key.value = "";
    showMode();
    changedDraft();
  });
  clearKey.addEventListener("click", () => {
    key.value = "";
    changedDraft();
    key.focus();
  });

  function open(connection) {
    useDefaultConnection = !connection;
    settingsDialog.classList.remove("decision-edited");
    invalidateCheck();
    mode.value = connection ? (["rule", "builtin"].includes(connection.provider) ? connection.provider : "model") :
      (operatorDefault ? "default" : "rule");
    showMode();
    provider.selectedIndex = 0;
    if (connection && mode.value === "model") provider.value = connection.provider;
    model.value = connection?.model || "";
    address.value = connection?.base_url ?? defaultAddress();
    key.value = keyAllowed ? connection?.api_key || "" : "";
  }

  function value() {
    if (useDefaultConnection || mode.value === "default") return undefined;
    if (mode.value === "rule" || mode.value === "builtin") return {provider: mode.value};
    return {provider: provider.value, base_url: address.value.trim(), model: model.value.trim(),
      api_key: keyAllowed ? key.value : ""};
  }

  function valid() {
    return mode.value !== "model" || (model.reportValidity() && address.reportValidity());
  }

  check.addEventListener("click", async () => {
    if (!valid()) return;
    const turn = ++generation;
    check.disabled = true;
    status.textContent = "연결 확인 중…";
    try {
      const connection = value();
      if (nativeDesktop) {
        await desktop.invoke("check_decision_connection", {connection});
      } else {
        const response = await fetch("/api/decision/check", {
          method: "POST", headers: {"Content-Type": "application/json"}, redirect: "error",
          body: JSON.stringify(connection),
        });
        const result = await response.json();
        if (!response.ok || result?.ok !== true) throw {code: result?.code};
      }
      if (turn === generation) status.textContent = "연결 확인 성공";
    } catch (error) {
      if (turn === generation) status.textContent = decisionMessage(error?.code) || decisionMessages.unavailable;
    } finally {
      if (turn === generation) check.disabled = false;
    }
  });
  return {open, value, valid, keyAllowed};
}
