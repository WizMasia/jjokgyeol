#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod safety;

#[cfg(not(windows))]
use std::process::{Command, Stdio};
#[cfg(any(not(unix), test))]
use std::time::Instant;
use std::{
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    time::Duration,
};
use tauri::{Emitter, Manager, State, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use tokio::sync::Semaphore;
#[cfg(windows)]
use windows_spawn::{Command, DropPolicy, SpawnOptions, Stdio};

struct Limits {
    request: u64,
    file: u64,
    timeout: Duration,
    slots: Arc<Semaphore>,
}

#[derive(Default)]
struct DirtyState {
    manuscript: AtomicBool,
    style: AtomicBool,
}

#[derive(Default)]
struct SettingsState {
    snapshot: Option<serde_json::Value>,
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
    .map_err(|e| e.to_string())?
    .build()
    .map_err(|e| e.to_string())?
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
    limits: State<'_, Limits>,
    snapshot: serde_json::Value,
) -> Result<(), String> {
    require_window(&window, "settings")?;
    validate_snapshot(&snapshot, limits.request)?;
    let main = window
        .app_handle()
        .get_webview_window("main")
        .ok_or("main window unavailable")?;
    main.emit_to("main", "settings-applied", snapshot)
        .map_err(|e| e.to_string())?;
    window.close().map_err(|e| e.to_string())
}

#[tauri::command]
fn cancel_settings(window: WebviewWindow) -> Result<(), String> {
    require_window(&window, "settings")?;
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

fn limits() -> Limits {
    let config: serde_json::Value =
        serde_json::from_str(include_str!("../../../app/config/operations.json"))
            .expect("invalid operations configuration");
    let number = |key: &str| config[key].as_u64().expect("missing operations limit");
    Limits {
        request: number("request_body_bytes"),
        file: number("worker_file_bytes"),
        timeout: Duration::from_secs(number("timeout_seconds")),
        slots: Arc::new(Semaphore::new(number("render_slots") as usize)),
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
    state: &Limits,
    f: impl FnOnce() -> Result<T, String> + Send + 'static,
) -> Result<T, String> {
    let permit = state
        .slots
        .clone()
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
    run_blocking(&state, move || {
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
    run_blocking(&state, move || {
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
    if !safety::within_limit(raw, state.file) {
        return Err("file exceeds limit".into());
    }
    let header_name = request
        .headers()
        .get("x-file-name")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let name = safety::decode_header_filename(header_name);
    let bytes = raw.to_vec();
    run_blocking(&state, move || {
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
        .manage(DirtyState::default())
        .manage(Mutex::new(SettingsState::default()))
        .manage(SettingsOpenLock::default())
        .invoke_handler(tauri::generate_handler![
            open_settings_window,
            settings_snapshot,
            apply_settings,
            cancel_settings,
            confirm_settings_replacement,
            style_convert,
            render_proof,
            save_bytes,
            set_dirty,
            set_style_dirty,
            confirm_document_replacement,
            confirm_style_replacement
        ])
        .on_window_event(|window, event| {
            if window.label() == "main" {
                match event {
                    tauri::WindowEvent::CloseRequested { api, .. } => {
                        if window.state::<DirtyState>().is_dirty() && !confirm_discard() {
                            api.prevent_close();
                        }
                    }
                    tauri::WindowEvent::Destroyed => {
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
