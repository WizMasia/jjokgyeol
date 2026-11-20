#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod safety;

#[cfg(not(windows))]
use std::process::{Command, Stdio};
use std::{
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::Arc,
    time::{Duration, Instant},
};
use tauri::{Manager, State};
use tokio::sync::Semaphore;
#[cfg(windows)]
use windows_spawn::{Command, DropPolicy, SpawnOptions, Stdio};

struct Limits {
    request: u64,
    file: u64,
    timeout: Duration,
    slots: Arc<Semaphore>,
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
    match std::env::var_os("JJOKGYEOL_NATIVE_RUNTIME") {
        Some(path) => Ok(path.into()),
        None => app
            .path()
            .resource_dir()
            .map(|p| p.join("runtime"))
            .map_err(|e| e.to_string()),
    }
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
    app: tauri::AppHandle,
    state: State<'_, Limits>,
    source: String,
    style: serde_json::Value,
) -> Result<tauri::ipc::Response, String> {
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
    app: tauri::AppHandle,
    state: State<'_, Limits>,
    action: String,
    body: String,
) -> Result<serde_json::Value, String> {
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
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(10)),
            Ok(None) => break Err("worker timed out".into()),
            Err(e) => break Err(e.to_string()),
        }
    };
    #[cfg(unix)]
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGKILL);
    }
    #[cfg(unix)]
    let _ = child.wait();
    #[cfg(windows)]
    drop(child);
    #[cfg(not(any(unix, windows)))]
    {
        let _ = child.kill();
        let _ = child.wait();
    }
    if !result?.success() {
        return Err(String::from_utf8_lossy(&read_limited(&errors, limit)?).into_owned());
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
    state: State<'_, Limits>,
    name: String,
    bytes: Vec<u8>,
) -> Result<bool, String> {
    if !safety::within_limit(&bytes, state.file) {
        return Err("file exceeds limit".into());
    }
    let name = safety::safe_name(&name);
    tauri::async_runtime::spawn_blocking(move || {
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
    .map_err(|e| e.to_string())?
}

fn main() {
    tauri::Builder::default()
        .manage(limits())
        .invoke_handler(tauri::generate_handler![
            style_convert,
            render_proof,
            save_bytes
        ])
        .run(tauri::generate_context!())
        .expect("failed to run 쪽결");
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

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
}
