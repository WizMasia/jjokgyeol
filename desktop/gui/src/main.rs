#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod fonts;
mod safety;

#[cfg(not(windows))]
use std::process::{Command, Stdio};
use std::{
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    time::{Duration, Instant},
};
use tauri::{Emitter, Manager, State, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use tokio::sync::Semaphore;
#[cfg(windows)]
use windows_spawn::{Command, DropPolicy, SpawnOptions, Stdio};

struct Limits {
    request: u64,
    file: u64,
    fonts: jjokgyeol_proof::DocumentFontLimits,
    timeout: Duration,
    render_slots: Arc<Semaphore>,
    style_slots: Arc<Semaphore>,
    save_slots: Arc<Semaphore>,
}

#[derive(Default)]
struct DirtyState {
    manuscript: AtomicBool,
    style: AtomicBool,
}

#[derive(Default)]
struct SettingsState {
    snapshot: Option<serde_json::Value>,
    revision: u64,
    pending: Option<serde_json::Value>,
}

#[derive(Default)]
struct SettingsOpenLock(tokio::sync::Mutex<()>);

fn validate_snapshot(snapshot: &serde_json::Value, limit: u64) -> Result<(), String> {
    if !snapshot.is_object() || !snapshot["style"].is_object() || !snapshot["dirty"].is_boolean() {
        return Err("invalid settings snapshot".into());
    }
    let bytes = serde_json::to_vec(snapshot).map_err(|e| e.to_string())?;
    if !safety::within_limit(&bytes, limit) {
        return Err("request exceeds limit".into());
    }
    Ok(())
}

impl SettingsState {
    fn open(
        &mut self,
        snapshot: serde_json::Value,
        exists: bool,
        limit: u64,
    ) -> Result<(), String> {
        validate_snapshot(&snapshot, limit)?;
        if !exists {
            self.snapshot = Some(snapshot);
        }
        Ok(())
    }

    fn close_draft(&mut self) {
        self.snapshot = None;
    }

    fn publish(
        &mut self,
        mut snapshot: serde_json::Value,
        emit: impl FnOnce(serde_json::Value) -> Result<(), String>,
    ) -> Result<(), String> {
        let fields = snapshot
            .as_object_mut()
            .ok_or("invalid settings snapshot")?;
        let connection = fields
            .remove("connection")
            .unwrap_or(serde_json::Value::Null);
        let revision = self
            .revision
            .checked_add(1)
            .ok_or("invalid settings revision")?;
        fields.insert("connectionRevision".into(), revision.into());
        // The lock remains held through emission so main can only take a committed revision.
        emit(snapshot)?;
        self.revision = revision;
        self.pending = Some(connection);
        self.close_draft();
        Ok(())
    }

    fn take_connection(&mut self, revision: u64) -> Result<serde_json::Value, String> {
        if revision != self.revision {
            return Err("settings apply superseded".into());
        }
        self.pending
            .take()
            .ok_or_else(|| "settings connection unavailable".into())
    }
}

fn require_window(window: &WebviewWindow, label: &str) -> Result<(), String> {
    if window.label() == label {
        Ok(())
    } else {
        Err("unauthorized window".into())
    }
}

/// 허용된 창 목록 중 하나에서 호출된 IPC 명령인지 검증합니다.
/// Verify an IPC command was invoked from one of the allowed window labels.
fn require_any_window(window: &WebviewWindow, allowed: &[&str]) -> Result<(), String> {
    if allowed.contains(&window.label()) {
        Ok(())
    } else {
        Err("unauthorized window".into())
    }
}

#[tauri::command]
async fn open_settings_window(
    window: WebviewWindow,
    state: State<'_, Mutex<SettingsState>>,
    open_lock: State<'_, SettingsOpenLock>,
    limits: State<'_, Limits>,
    snapshot: serde_json::Value,
) -> Result<(), String> {
    require_window(&window, "main")?;
    let app = window.app_handle();
    let _opening = open_lock.0.lock().await;
    let existing = app.get_webview_window("settings");
    state
        .lock()
        .map_err(|e| e.to_string())?
        .open(snapshot, existing.is_some(), limits.request)?;
    if let Some(settings) = existing {
        return settings.set_focus().map_err(|e| e.to_string());
    }
    WebviewWindowBuilder::new(
        app,
        "settings",
        WebviewUrl::App("index.html?view=settings".into()),
    )
    .title("출판 설정")
    .inner_size(920.0, 720.0)
    .min_inner_size(360.0, 500.0)
    .parent(&window)
    .and_then(|builder| builder.build())
    .map_err(|error| {
        if let Ok(mut draft) = state.lock() {
            draft.close_draft();
        }
        error.to_string()
    })?
    .set_focus()
    .map_err(|e| e.to_string())
}

#[tauri::command]
fn settings_snapshot(
    window: WebviewWindow,
    state: State<'_, Mutex<SettingsState>>,
) -> Result<serde_json::Value, String> {
    require_window(&window, "settings")?;
    state
        .lock()
        .map_err(|e| e.to_string())?
        .snapshot
        .clone()
        .ok_or_else(|| "settings snapshot unavailable".into())
}

#[tauri::command]
fn apply_settings(
    window: WebviewWindow,
    state: State<'_, Mutex<SettingsState>>,
    limits: State<'_, Limits>,
    snapshot: serde_json::Value,
) -> Result<(), String> {
    require_window(&window, "settings")?;
    validate_snapshot(&snapshot, limits.request)?;
    let main = window
        .app_handle()
        .get_webview_window("main")
        .ok_or("main window unavailable")?;
    state
        .lock()
        .map_err(|e| e.to_string())?
        .publish(snapshot, |public| {
            main.emit_to("main", "settings-applied", public)
                .map_err(|e| e.to_string())
        })?;
    window.close().map_err(|e| e.to_string())
}

#[tauri::command]
fn take_settings_connection(
    window: WebviewWindow,
    state: State<'_, Mutex<SettingsState>>,
    revision: u64,
) -> Result<serde_json::Value, String> {
    require_window(&window, "main")?;
    state
        .lock()
        .map_err(|e| e.to_string())?
        .take_connection(revision)
}

#[tauri::command]
fn cancel_settings(
    window: WebviewWindow,
    state: State<'_, Mutex<SettingsState>>,
) -> Result<(), String> {
    require_window(&window, "settings")?;
    state.lock().map_err(|e| e.to_string())?.close_draft();
    window.close().map_err(|e| e.to_string())
}

#[tauri::command]
fn confirm_settings_replacement(window: WebviewWindow) -> Result<bool, String> {
    require_window(&window, "settings")?;
    Ok(confirm_discard())
}

impl DirtyState {
    fn is_dirty(&self) -> bool {
        self.manuscript.load(Ordering::Relaxed) || self.style.load(Ordering::Relaxed)
    }
}

fn confirm_discard() -> bool {
    rfd::MessageDialog::new()
        .set_title("쪽결")
        .set_description("저장하지 않은 변경 사항이 있습니다. 계속하시겠습니까?")
        .set_buttons(rfd::MessageButtons::YesNo)
        .show()
        == rfd::MessageDialogResult::Yes
}

#[tauri::command]
fn set_dirty(
    window: WebviewWindow,
    state: State<'_, DirtyState>,
    dirty: bool,
) -> Result<(), String> {
    require_window(&window, "main")?;
    state.manuscript.store(dirty, Ordering::Relaxed);
    Ok(())
}

#[tauri::command]
fn set_style_dirty(
    window: WebviewWindow,
    state: State<'_, DirtyState>,
    dirty: bool,
) -> Result<(), String> {
    require_window(&window, "main")?;
    state.style.store(dirty, Ordering::Relaxed);
    Ok(())
}

#[tauri::command]
fn confirm_document_replacement(
    window: WebviewWindow,
    state: State<'_, DirtyState>,
) -> Result<bool, String> {
    require_window(&window, "main")?;
    Ok(!state.manuscript.load(Ordering::Relaxed) || confirm_discard())
}

#[tauri::command]
fn confirm_style_replacement(
    window: WebviewWindow,
    state: State<'_, DirtyState>,
) -> Result<bool, String> {
    require_window(&window, "main")?;
    Ok(!state.style.load(Ordering::Relaxed) || confirm_discard())
}

impl Limits {
    fn save_limit(&self, name: &str) -> u64 {
        if Path::new(name)
            .extension()
            .is_some_and(|ext| ext.eq_ignore_ascii_case("jjok"))
        {
            self.fonts.archive_bytes
        } else {
            self.file
        }
    }
}

fn limits() -> Limits {
    let config: serde_json::Value =
        serde_json::from_str(include_str!("../../../app/config/operations.json"))
            .expect("invalid operations configuration");
    let number = |key: &str| config[key].as_u64().expect("missing operations limit");
    let slot_count = number("render_slots") as usize;
    Limits {
        request: number("request_body_bytes"),
        file: number("worker_file_bytes"),
        fonts: jjokgyeol_proof::DocumentFontLimits::configured(),
        timeout: Duration::from_secs(number("timeout_seconds")),
        render_slots: Arc::new(Semaphore::new(slot_count)),
        // Saving needs style conversion during renders; previews need it while save dialogs are open.
        style_slots: Arc::new(Semaphore::new(slot_count)),
        save_slots: Arc::new(Semaphore::new(slot_count)),
    }
}

fn runtime(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    // 릴리스 빌드에서는 번들 리소스 외 임의 런타임 주입을 차단합니다.
    // Block arbitrary runtime directory injection in release builds.
    #[cfg(debug_assertions)]
    if let Some(path) = std::env::var_os("JJOKGYEOL_NATIVE_RUNTIME") {
        return Ok(path.into());
    }
    tauri::utils::platform::resource_dir(app.package_info(), &app.env())
        .map(|dir| dir.join("runtime"))
        .map_err(|error| {
            format!("앱의 조판 리소스 폴더를 찾을 수 없습니다. 설치 패키지에서 쪽결을 다시 열어 주세요. ({error})")
        })
}

async fn run_blocking<T: Send + 'static>(
    slots: &Arc<Semaphore>,
    f: impl FnOnce() -> Result<T, String> + Send + 'static,
) -> Result<T, String> {
    let permit = Arc::clone(slots)
        .acquire_owned()
        .await
        .map_err(|e| e.to_string())?;
    tauri::async_runtime::spawn_blocking(move || {
        let _permit = permit;
        f()
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn render_proof(
    window: WebviewWindow,
    app: tauri::AppHandle,
    state: State<'_, Limits>,
    source: String,
    style: serde_json::Value,
) -> Result<tauri::ipc::Response, String> {
    require_window(&window, "main")?;
    let request = serde_json::to_vec(&serde_json::json!({"source": &source, "style": &style}))
        .map_err(|e| e.to_string())?;
    if !safety::within_limit(&request, state.request) {
        return Err("request exceeds limit".into());
    }
    let runtime = runtime(&app)?;
    let limit = state.file;
    run_blocking(&state.render_slots, move || {
        let work = tempfile::tempdir().map_err(|e| e.to_string())?;
        let source_path = work.path().join("source.md");
        let style_path = work.path().join("style.json");
        let pdf_path = work.path().join("proof.pdf");
        fs::write(&source_path, source).map_err(|e| e.to_string())?;
        fs::write(
            &style_path,
            serde_json::to_vec(&style).map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())?;
        jjokgyeol_proof::render(&runtime, &source_path, &style_path, &pdf_path)
            .map_err(|e| e.to_string())?;
        Ok(tauri::ipc::Response::new(read_limited(&pdf_path, limit)?))
    })
    .await
}

fn decision_body(value: &serde_json::Value, limit: u64) -> Result<Vec<u8>, String> {
    let body =
        serde_json::to_vec(value).map_err(|_| r#"{"code":"invalid_connection"}"#.to_owned())?;
    if !safety::within_limit(&body, limit) {
        return Err(r#"{"code":"invalid_connection"}"#.into());
    }
    Ok(body)
}

async fn run_with_deadline<T: Send + 'static>(
    slots: &Arc<Semaphore>,
    deadline: Instant,
    f: impl FnOnce(Duration) -> Result<T, String> + Send + 'static,
) -> Result<T, String> {
    const TIMEOUT: &str = r#"{"code":"timeout"}"#;
    let permit = tokio::time::timeout_at(deadline.into(), Arc::clone(slots).acquire_owned())
        .await
        .map_err(|_| TIMEOUT)?
        .map_err(|_| r#"{"code":"unavailable"}"#)?;
    let (start, started) = tokio::sync::oneshot::channel();
    let worker = tokio::task::spawn_blocking(move || {
        let _permit = permit;
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() || start.send(()).is_err() {
            return Err(TIMEOUT.into());
        }
        f(remaining)
    });
    // Once started, the process supervisor owns the remaining deadline and cleanup.
    if tokio::time::timeout_at(deadline.into(), started)
        .await
        .is_err()
    {
        worker.abort();
        return Err(TIMEOUT.into());
    }
    worker
        .await
        .map_err(|_| r#"{"code":"unavailable"}"#.to_owned())?
}

#[tauri::command]
async fn check_decision_connection(
    window: WebviewWindow,
    app: tauri::AppHandle,
    state: State<'_, Limits>,
    connection: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let deadline = Instant::now() + jjokgyeol_proof::decision_check_timeout(state.timeout);
    require_any_window(&window, &["main", "settings"])?;
    let body = decision_body(&connection, state.request)?;
    let runtime = runtime(&app).map_err(|_| r#"{"code":"unavailable"}"#.to_owned())?;
    run_with_deadline(&state.render_slots, deadline, move |timeout| {
        jjokgyeol_proof::check_decision_connection(&runtime, body, timeout)?;
        Ok(serde_json::json!({"ok":true}))
    })
    .await
}

#[tauri::command]
async fn render_model_proof(
    window: WebviewWindow,
    app: tauri::AppHandle,
    state: State<'_, Limits>,
    source: String,
    style: serde_json::Value,
    connection: serde_json::Value,
) -> Result<tauri::ipc::Response, String> {
    let deadline = Instant::now() + state.timeout;
    require_window(&window, "main")?;
    let body = decision_body(
        &serde_json::json!({"source":source, "style":style, "connection":connection}),
        state.request,
    )?;
    decision_proof(&app, &state, body, deadline).await
}

#[tauri::command]
async fn render_font_proof(
    window: WebviewWindow,
    app: tauri::AppHandle,
    state: State<'_, Limits>,
    request: tauri::ipc::Request<'_>,
) -> Result<tauri::ipc::Response, String> {
    let deadline = Instant::now() + state.timeout;
    require_window(&window, "main")?;
    let tauri::ipc::InvokeBody::Raw(bytes) = request.body() else {
        return Err("글꼴 조판에는 바이너리 요청이 필요합니다.".into());
    };
    if !state.fonts.accepts_frame(bytes, state.request) {
        return Err("글꼴 요청의 크기와 형식을 확인하세요.".into());
    }
    decision_proof(&app, &state, bytes.to_vec(), deadline).await
}

async fn decision_proof(
    app: &tauri::AppHandle,
    state: &Limits,
    body: Vec<u8>,
    deadline: Instant,
) -> Result<tauri::ipc::Response, String> {
    let runtime = runtime(app).map_err(|_| r#"{"code":"unavailable"}"#.to_owned())?;
    run_with_deadline(&state.render_slots, deadline, move |timeout| {
        jjokgyeol_proof::render_model_proof(&runtime, body, timeout).map(tauri::ipc::Response::new)
    })
    .await
}

#[tauri::command]
async fn list_fonts(
    window: WebviewWindow,
    state: State<'_, Mutex<Arc<fonts::Catalog>>>,
    limits: State<'_, Limits>,
) -> Result<Vec<serde_json::Value>, String> {
    require_any_window(&window, &["main", "settings"])?;
    let catalog = run_blocking(&limits.style_slots, fonts::installed).await?;
    let items = catalog.list();
    *state.lock().map_err(|e| e.to_string())? = Arc::new(catalog);
    Ok(items)
}

#[tauri::command]
async fn read_font(
    window: WebviewWindow,
    state: State<'_, Mutex<Arc<fonts::Catalog>>>,
    limits: State<'_, Limits>,
    id: String,
) -> Result<tauri::ipc::Response, String> {
    require_any_window(&window, &["main", "settings"])?;
    let catalog = state.lock().map_err(|e| e.to_string())?.clone();
    let limit = limits.fonts.file_bytes;
    run_blocking(&limits.style_slots, move || {
        catalog.read(&id, limit).map(tauri::ipc::Response::new)
    })
    .await
}

#[tauri::command]
async fn builtin_font(
    window: WebviewWindow,
    app: tauri::AppHandle,
    limits: State<'_, Limits>,
    hash: String,
) -> Result<tauri::ipc::Response, String> {
    require_any_window(&window, &["main", "settings"])?;
    let runtime = runtime(&app)?;
    let limit = limits.fonts.file_bytes;
    run_blocking(&limits.style_slots, move || {
        fonts::builtin(&runtime, &hash, limit).map(tauri::ipc::Response::new)
    })
    .await
}

fn read_limited(path: &Path, limit: u64) -> Result<Vec<u8>, String> {
    let mut bytes = Vec::new();
    fs::File::open(path)
        .map_err(|e| e.to_string())?
        .take(limit.saturating_add(1))
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if !safety::within_limit(&bytes, limit) {
        return Err("worker output exceeds limit".into());
    }
    Ok(bytes)
}

fn paragraph_error(error: String) -> serde_json::Value {
    if matches!(error.as_str(), r#"{"code":"timeout"}"# | "worker timed out") {
        return serde_json::json!({"code":"timeout","status":504,"message":"문단 정렬 시간이 초과되었습니다."});
    }
    if let Ok(value) = serde_json::from_str::<serde_json::Value>(&error) {
        if value["code"] == "invalid_input" {
            return value;
        }
    }
    serde_json::json!({"code":"worker_error","status":500,"message":"문단 정렬을 처리할 수 없습니다."})
}

#[tauri::command]
async fn paragraph_align(
    window: WebviewWindow,
    app: tauri::AppHandle,
    state: State<'_, Limits>,
    payload: serde_json::Value,
) -> Result<serde_json::Value, serde_json::Value> {
    let deadline = Instant::now() + state.timeout;
    require_window(&window, "main")?;
    let body = payload.to_string();
    if !safety::within_limit(body.as_bytes(), state.request) {
        return Err(
            serde_json::json!({"code":"too_large","status":413,"message":"요청 크기가 너무 큽니다."}),
        );
    }
    let runtime = runtime(&app).map_err(paragraph_error)?;
    let limit = state.file;
    run_with_deadline(&state.style_slots, deadline, move |timeout| {
        convert(&runtime, "--paragraph-align", &body, limit, timeout)
    })
    .await
    .map_err(paragraph_error)
}

#[tauri::command]
async fn style_convert(
    window: WebviewWindow,
    app: tauri::AppHandle,
    state: State<'_, Limits>,
    action: String,
    body: String,
) -> Result<serde_json::Value, String> {
    require_any_window(&window, &["main", "settings"])?;
    let flag = match action.as_str() {
        "open" => "--style-open",
        "export" => "--style-export",
        _ => return Err("invalid style action".into()),
    };
    if !safety::within_limit(body.as_bytes(), state.request) {
        return Err("request exceeds limit".into());
    }
    let runtime = runtime(&app)?;
    let limit = state.file;
    let timeout = state.timeout;
    run_blocking(&state.style_slots, move || {
        convert(&runtime, flag, &body, limit, timeout)
    })
    .await
}

fn convert(
    runtime: &Path,
    flag: &str,
    body: &str,
    limit: u64,
    timeout: Duration,
) -> Result<serde_json::Value, String> {
    let work = tempfile::tempdir().map_err(|e| e.to_string())?;
    let input = work.path().join("input");
    let output = work.path().join("output");
    let errors = work.path().join("errors");
    fs::write(&input, body).map_err(|e| e.to_string())?;
    let mut command = Command::new(
        runtime
            .join("worker")
            .join(format!("jjokgyeol-worker{}", std::env::consts::EXE_SUFFIX)),
    );
    command
        .env_clear()
        .envs(jjokgyeol_proof::worker_env_pairs())
        .arg(flag)
        .current_dir(work.path())
        .stdin(Stdio::from(
            fs::File::open(input).map_err(|e| e.to_string())?,
        ))
        .stdout(Stdio::from(
            fs::File::create(&output).map_err(|e| e.to_string())?,
        ))
        .stderr(Stdio::from(
            fs::File::create(&errors).map_err(|e| e.to_string())?,
        ));
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    #[cfg(not(windows))]
    let mut child = command.spawn().map_err(|e| e.to_string())?;
    #[cfg(windows)]
    let mut child = command
        .spawn_with(SpawnOptions::new().drop_policy(DropPolicy::KillTree))
        .map_err(|e| e.to_string())?;
    #[cfg(unix)]
    let status = jjokgyeol_proof::wait_unix_process_group(&mut child, timeout, || {
        if fs::metadata(&output)
            .map(|m| m.len() > limit)
            .unwrap_or(false)
            || fs::metadata(&errors)
                .map(|m| m.len() > limit)
                .unwrap_or(false)
        {
            return Err("worker output exceeds limit".to_string());
        }
        Ok(())
    })?;
    #[cfg(not(unix))]
    let status = {
        let deadline = Instant::now() + timeout;
        let result = loop {
            if fs::metadata(&output)
                .map(|m| m.len() > limit)
                .unwrap_or(false)
                || fs::metadata(&errors)
                    .map(|m| m.len() > limit)
                    .unwrap_or(false)
            {
                break Err("worker output exceeds limit".to_string());
            }
            match child.try_wait() {
                Ok(Some(status)) => break Ok(status),
                Ok(None) if Instant::now() < deadline => {
                    std::thread::sleep(Duration::from_millis(10))
                }
                Ok(None) => break Err("worker timed out".into()),
                Err(e) => break Err(e.to_string()),
            }
        };
        #[cfg(windows)]
        drop(child);
        #[cfg(not(windows))]
        {
            let _ = child.kill();
            let _ = child.wait();
        }
        result?
    };
    if !status.success() {
        let mut err_file = fs::File::open(&errors).map_err(|e| e.to_string())?;
        return Err(jjokgyeol_proof::sanitize_worker_error(&mut err_file));
    }
    let value: serde_json::Value =
        serde_json::from_slice(&read_limited(&output, limit)?).map_err(|e| e.to_string())?;
    if !value.is_object() {
        return Err("worker returned non-object JSON".into());
    }
    Ok(value)
}

#[tauri::command]
async fn save_bytes(
    window: WebviewWindow,
    state: State<'_, Limits>,
    request: tauri::ipc::Request<'_>,
) -> Result<bool, String> {
    require_any_window(&window, &["main", "settings"])?;
    let tauri::ipc::InvokeBody::Raw(raw) = request.body() else {
        return Err("invalid file payload".into());
    };
    let header_name = request
        .headers()
        .get("x-file-name")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let name = safety::decode_header_filename(header_name);
    if !safety::within_limit(raw, state.save_limit(&name)) {
        return Err("file exceeds limit".into());
    }
    let bytes = raw.to_vec();
    run_blocking(&state.save_slots, move || {
        let Some(path) = rfd::FileDialog::new().set_file_name(&name).save_file() else {
            return Ok(false);
        };
        let parent = path.parent().ok_or("invalid save path")?;
        let mut temp = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
        temp.write_all(&bytes).map_err(|e| e.to_string())?;
        temp.persist(path).map_err(|e| e.to_string())?;
        Ok(true)
    })
    .await
}

fn main() {
    tauri::Builder::default()
        .manage(limits())
        .manage(Mutex::new(Arc::new(fonts::Catalog::default())))
        .manage(DirtyState::default())
        .manage(Mutex::new(SettingsState::default()))
        .manage(SettingsOpenLock::default())
        .invoke_handler(tauri::generate_handler![
            open_settings_window,
            settings_snapshot,
            apply_settings,
            take_settings_connection,
            cancel_settings,
            confirm_settings_replacement,
            style_convert,
            paragraph_align,
            render_proof,
            check_decision_connection,
            render_model_proof,
            render_font_proof,
            list_fonts,
            read_font,
            builtin_font,
            save_bytes,
            set_dirty,
            set_style_dirty,
            confirm_document_replacement,
            confirm_style_replacement
        ])
        .on_window_event(|window, event| {
            if window.label() == "settings" && matches!(event, tauri::WindowEvent::Destroyed) {
                if let Ok(mut state) = window.state::<Mutex<SettingsState>>().lock() {
                    state.close_draft();
                }
            }
            if window.label() == "main" {
                match event {
                    tauri::WindowEvent::CloseRequested { api, .. } => {
                        if window.state::<DirtyState>().is_dirty() && !confirm_discard() {
                            api.prevent_close();
                        }
                    }
                    tauri::WindowEvent::Destroyed => {
                        if let Ok(mut state) = window.state::<Mutex<SettingsState>>().lock() {
                            *state = SettingsState::default();
                        }
                        if let Some(settings) = window.app_handle().get_webview_window("settings") {
                            let _ = settings.destroy();
                        }
                    }
                    _ => {}
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("failed to run 쪽결");
}

#[test]
fn document_save_runs_with_busy_renders_and_leaves_render_capacity_free() {
    use std::sync::mpsc;

    let state = limits();
    let render_slots = Arc::clone(&state.render_slots);
    let capacity = render_slots.available_permits().try_into().unwrap();
    let busy_renders = render_slots.try_acquire_many(capacity).unwrap();
    let (opened, dialog_open) = mpsc::channel();
    let (close, dialog_closed) = mpsc::channel();

    let save = tauri::async_runtime::spawn(async move {
        run_blocking(&state.style_slots, || Ok(())).await.unwrap();
        run_blocking(&state.save_slots, move || {
            let _ = opened.send(());
            let _ = dialog_closed.recv();
            Ok(())
        })
        .await
        .unwrap();
    });
    dialog_open
        .recv_timeout(Duration::from_secs(3))
        .expect("style conversion and save must start while every render slot is busy");
    drop(busy_renders);
    let _next_renders = render_slots
        .try_acquire_many(capacity)
        .expect("a waiting save dialog must leave every render slot free");
    close.send(()).unwrap();
    tauri::async_runtime::block_on(save).unwrap();
}

#[test]
fn full_save_capacity_still_allows_preview_style_conversion() {
    let state = limits();
    let save_slots = &state.save_slots;
    let capacity = save_slots.available_permits().try_into().unwrap();
    let _open_dialogs = save_slots.try_acquire_many(capacity).unwrap();
    assert!(
        state.style_slots.try_acquire().is_ok(),
        "preview style conversion must proceed while all save dialogs are busy"
    );
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    #[test]
    fn settings_snapshot_keeps_existing_draft_and_replaces_after_close() {
        let mut state = SettingsState::default();
        let first = serde_json::json!({"style": {"name": "first"}, "dirty": true});
        let next = serde_json::json!({"style": {"name": "next"}, "dirty": false});
        state.open(first.clone(), false, 1024).unwrap();
        state.open(next.clone(), true, 1024).unwrap();
        assert_eq!(state.snapshot, Some(first));
        state.open(next.clone(), false, 1024).unwrap();
        assert_eq!(state.snapshot, Some(next));
    }

    #[test]
    fn settings_rejects_invalid_or_oversized_snapshots_without_overwriting() {
        let mut state = SettingsState::default();
        let first = serde_json::json!({"style": {}, "dirty": false});
        state.open(first.clone(), false, 1024).unwrap();
        assert!(state
            .open(
                serde_json::json!({"style": {}, "dirty": "yes"}),
                false,
                1024
            )
            .is_err());
        assert!(state
            .open(serde_json::json!({"style": {}, "dirty": false}), false, 2)
            .is_err());
        assert_eq!(state.snapshot, Some(first));
    }

    #[test]
    fn manuscript_and_style_dirty_states_clear_independently() {
        let state = DirtyState::default();
        assert!(!state.is_dirty());
        state.manuscript.store(true, Ordering::Relaxed);
        state.style.store(true, Ordering::Relaxed);
        state.manuscript.store(false, Ordering::Relaxed);
        assert!(state.is_dirty());
        state.style.store(false, Ordering::Relaxed);
        assert!(!state.is_dirty());
    }

    #[test]
    fn style_worker_receives_mode_and_input_and_returns_object() {
        let root = tempfile::tempdir().unwrap();
        let worker_dir = root.path().join("worker");
        fs::create_dir(&worker_dir).unwrap();
        let worker = worker_dir.join("jjokgyeol-worker");
        fs::write(
            &worker,
            "#!/bin/sh\n[ \"$1\" = --style-open ] || exit 1\ncat\n",
        )
        .unwrap();
        fs::set_permissions(&worker, fs::Permissions::from_mode(0o755)).unwrap();
        let result = convert(
            root.path(),
            "--style-open",
            "{\"이름\":\"양식\"}",
            1024,
            Duration::from_secs(1),
        )
        .unwrap();
        assert_eq!(result["이름"], "양식");
        assert!(convert(
            root.path(),
            "--style-export",
            "{}",
            1024,
            Duration::from_secs(1)
        )
        .is_err());
        assert!(convert(
            root.path(),
            "--style-open",
            "123456789",
            4,
            Duration::from_secs(1)
        )
        .is_err());
    }

    #[test]
    fn style_worker_is_stopped_at_deadline() {
        let root = tempfile::tempdir().unwrap();
        let worker_dir = root.path().join("worker");
        fs::create_dir(&worker_dir).unwrap();
        let worker = worker_dir.join("jjokgyeol-worker");
        fs::write(&worker, "#!/bin/sh\nsleep 10\n").unwrap();
        fs::set_permissions(&worker, fs::Permissions::from_mode(0o755)).unwrap();
        let start = Instant::now();
        assert!(convert(
            root.path(),
            "--style-open",
            "{}",
            1024,
            Duration::from_millis(50)
        )
        .is_err());
        assert!(start.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn style_worker_sanitizes_stderr_and_strips_secret_env() {
        let root = tempfile::tempdir().unwrap();
        let worker_dir = root.path().join("worker");
        fs::create_dir(&worker_dir).unwrap();
        let worker = worker_dir.join("jjokgyeol-worker");
        // Verify non-JSON stderr is sanitized and never leaked raw.
        fs::write(
            &worker,
            "#!/bin/sh\necho 'Traceback: /private/secret/path' >&2\nexit 1\n",
        )
        .unwrap();
        fs::set_permissions(&worker, fs::Permissions::from_mode(0o755)).unwrap();
        let err = convert(
            root.path(),
            "--style-open",
            "{}",
            1024,
            Duration::from_secs(1),
        )
        .unwrap_err();
        assert_eq!(err, "worker failed");

        // Verify valid invalid_input JSON passes through only allowed fields.
        fs::write(
            &worker,
            "#!/bin/sh\nprintf '{\"code\":\"invalid_input\",\"field\":\"body_pt\",\"message\":\"크기 오류\",\"secret\":\"hidden\"}' >&2\nexit 1\n",
        )
        .unwrap();
        let err2 = convert(
            root.path(),
            "--style-open",
            "{}",
            1024,
            Duration::from_secs(1),
        )
        .unwrap_err();
        let parsed: serde_json::Value = serde_json::from_str(&err2).unwrap();
        assert_eq!(
            parsed,
            serde_json::json!({"code": "invalid_input", "field": "body_pt", "message": "크기 오류"})
        );
    }
}

#[test]
fn paragraph_errors_keep_validation_fields_and_distinguish_deadlines() {
    let invalid = serde_json::json!({"code":"invalid_input","field":"align","message":"정렬 값을 확인하세요."});
    assert_eq!(paragraph_error(invalid.to_string()), invalid);
    for error in [r#"{"code":"timeout"}"#, "worker timed out"] {
        let reply = paragraph_error(error.to_owned());
        assert_eq!(reply["code"], "timeout");
        assert_eq!(reply["status"], 504);
    }
    let reply = paragraph_error("failed to open /private/path".to_owned());
    assert_eq!(reply["status"], 500);
    assert!(!reply.to_string().contains("/private/path"));
}

#[test]
fn decision_ipc_limits_include_connection_before_queueing() {
    let value =
        serde_json::json!({"source":"원고", "style":{}, "connection":{"api_key":"test-key"}});
    let bytes = serde_json::to_vec(&value).unwrap();
    let limit = bytes.len() as u64;
    assert_eq!(decision_body(&value, limit).unwrap(), bytes);
    let error = decision_body(&value, limit - 1).unwrap_err();
    assert_eq!(
        serde_json::from_str::<serde_json::Value>(&error).unwrap(),
        serde_json::json!({"code":"invalid_connection"})
    );
}

#[test]
fn settings_events_transfer_only_the_latest_private_connection_once() {
    let mut state = SettingsState::default();
    let mut public_events = Vec::new();
    for key in ["first-private", "next-private"] {
        let snapshot = serde_json::json!({"style":{"n":1},"dirty":false,"baseRevision":7,
                                          "connection":{"provider":"chat","api_key":key}});
        state.open(snapshot.clone(), false, 1024).unwrap();
        state
            .publish(snapshot, |event| {
                public_events.push(event);
                Ok(())
            })
            .unwrap();
        assert!(state.snapshot.is_none());
    }
    for (index, event) in public_events.iter().enumerate() {
        assert_eq!(
            event,
            &serde_json::json!({"style":{"n":1},"dirty":false,"baseRevision":7,"connectionRevision":index+1})
        );
    }
    assert!(state.take_connection(1).is_err());
    assert_eq!(state.take_connection(2).unwrap()["api_key"], "next-private");
    assert!(state.take_connection(2).is_err());
}

#[test]
fn failed_settings_event_and_canceled_draft_preserve_previous_apply() {
    let mut state = SettingsState::default();
    let apply = serde_json::json!({"style":{}, "dirty":false, "connection":{"provider":"rule"}});
    state.publish(apply.clone(), |_| Ok(())).unwrap();
    state.open(apply.clone(), false, 1024).unwrap();
    assert!(state.publish(apply, |_| Err("closed".into())).is_err());
    state.close_draft();
    assert!(state.snapshot.is_none());
    assert_eq!(
        state.take_connection(1).unwrap(),
        serde_json::json!({"provider":"rule"})
    );
    state
        .publish(serde_json::json!({"style":{}, "dirty":false}), |_| Ok(()))
        .unwrap();
    assert_eq!(state.take_connection(2).unwrap(), serde_json::Value::Null);
}

#[test]
fn decision_queue_expires_before_a_slot_is_free() {
    tauri::async_runtime::block_on(async {
        let slots = Arc::new(Semaphore::new(0));
        let limit = Instant::now() + Duration::from_millis(50);
        let result: Result<(), _> = tokio::time::timeout(
            Duration::from_secs(2),
            run_with_deadline(&slots, limit, |_| panic!("late provider call")),
        )
        .await
        .expect("a blocked decision must return at its deadline");
        assert_eq!(result.unwrap_err(), r#"{"code":"timeout"}"#);
        slots.add_permits(1);
        assert!(slots.try_acquire().is_ok());
    });
}

#[test]
fn decision_supervisor_gets_only_the_remaining_budget() {
    tauri::async_runtime::block_on(async {
        let slots = Arc::new(Semaphore::new(0));
        let release = Arc::clone(&slots);
        let deadline = Instant::now() + Duration::from_secs(2);
        let unlock = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(100));
            release.add_permits(1);
        });
        let budget = run_with_deadline(&slots, deadline, Ok).await.unwrap();
        unlock.join().unwrap();
        assert!(!budget.is_zero() && budget <= Duration::from_millis(1900));
    });
}

#[test]
fn expired_decision_never_starts_after_blocking_pool_frees_up() {
    use std::sync::mpsc;

    let runtime = tokio::runtime::Builder::new_current_thread()
        .max_blocking_threads(1)
        .enable_all()
        .build()
        .unwrap();
    runtime.block_on(async {
        let slots = Arc::new(Semaphore::new(1));
        let (release, held) = mpsc::channel::<()>();
        let blocker =
            tokio::task::spawn_blocking(move || held.recv_timeout(Duration::from_secs(2)));
        let calls = Arc::new(AtomicBool::new(false));
        let invoked = Arc::clone(&calls);
        let start = Instant::now();
        let result = run_with_deadline(&slots, start + Duration::from_millis(50), move |_| {
            invoked.store(true, Ordering::SeqCst);
            Ok(())
        })
        .await;
        assert_eq!(result.unwrap_err(), r#"{"code":"timeout"}"#);
        assert!(start.elapsed() < Duration::from_secs(1));
        release.send(()).unwrap();
        blocker.await.unwrap().unwrap();
        tokio::task::spawn_blocking(|| {}).await.unwrap();
        assert!(!calls.load(Ordering::SeqCst));
        assert!(slots.try_acquire().is_ok());
    });
}

#[cfg(test)]
mod font_tests {
    use super::*;
    const FONT: &[u8] =
        include_bytes!("../../../app/static/standard_fonts/LiberationSans-Regular.ttf");

    #[test]
    fn only_listed_static_fonts_can_be_read_within_the_file_cap() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("한글.ttf");
        fs::write(&path, FONT).unwrap();
        let catalog = fonts::Catalog::new(vec![(path.clone(), "한글".into())]);
        let items = catalog.list();
        let id = items[0]["id"].as_str().unwrap();
        assert_eq!(items[0]["name"], "한글");
        assert!(!items[0].to_string().contains(dir.path().to_str().unwrap()));
        assert!(catalog.read(path.to_str().unwrap(), u64::MAX).is_err());
        assert_eq!(catalog.read(id, FONT.len() as u64).unwrap(), FONT);
        assert!(catalog.read(id, FONT.len() as u64 - 1).is_err());
        fs::write(&path, b"not font data").unwrap();
        assert!(catalog.read(id, u64::MAX).is_err());
        let mut broken = FONT.to_vec();
        broken[20..24].copy_from_slice(&u32::MAX.to_be_bytes());
        fs::write(&path, broken).unwrap();
        assert!(catalog.read(id, u64::MAX).is_err());
    }

    #[test]
    fn os_source_lists_a_readable_font_without_exposing_paths() {
        let catalog = fonts::installed().unwrap();
        let items = catalog.list();
        assert!(items.iter().any(|item| item["reason"].is_null()
            && catalog
                .read(item["id"].as_str().unwrap(), limits().fonts.file_bytes)
                .is_ok()));
    }

    #[test]
    fn builtin_only_returns_locked_contents() {
        let notice = "1c09d5a13702fa8ee5cd5fa160f26ef72ec1168997673230050f9c0801da5017";
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("fonts")).unwrap();
        fs::write(
            root.path().join("runtime.json"),
            r#"{"engine":"bin/lualatex","font_dir":"fonts"}"#,
        )
        .unwrap();
        let dir = root.path().join("licenses/debian");
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("fonts-nanum.copyright"), b"modified").unwrap();
        assert!(fonts::builtin(root.path(), notice, 16777216).is_err());
        assert!(fonts::builtin(
            root.path(),
            "../licenses/debian/fonts-nanum.copyright",
            16777216
        )
        .is_err());
        if let Some(runtime) = std::env::var_os("JJOKGYEOL_NATIVE_RUNTIME") {
            let lock: serde_json::Value =
                serde_json::from_str(include_str!("../../../runtime.lock")).unwrap();
            let faces = lock["fonts"]
                .as_object()
                .unwrap()
                .values()
                .flat_map(|family| family.as_object().unwrap().values());
            let extras = lock["document_font_files"]
                .as_object()
                .into_iter()
                .flat_map(|files| files.values());
            for face in faces.chain(extras) {
                assert!(!fonts::builtin(
                    Path::new(&runtime),
                    face["sha256"].as_str().unwrap(),
                    16777216
                )
                .unwrap()
                .is_empty());
            }
            assert!(String::from_utf8(
                fonts::builtin(Path::new(&runtime), notice, 16777216).unwrap()
            )
            .unwrap()
            .contains("OPEN FONT LICENSE"));
        }
    }

    #[test]
    fn only_document_saves_receive_the_larger_archive_cap() {
        let limits = limits();
        assert_eq!(limits.save_limit("원고.JJOK"), 134217728);
        assert_eq!(limits.save_limit("원고.jjok.pdf"), 67108864);
    }
}
