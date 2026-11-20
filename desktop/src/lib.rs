#[cfg(unix)]
use std::os::unix::process::CommandExt;
#[cfg(unix)]
use std::process::Command;
use std::{
    error::Error,
    fs,
    io::{Read, Seek, SeekFrom, Write},
    path::Path,
    time::{Duration, Instant},
};

const WORKER_ERROR_LIMIT: u64 = 16 * 1024;

pub fn render(
    runtime: &Path,
    source: &Path,
    style: &Path,
    output: &Path,
) -> Result<(), Box<dyn Error>> {
    let operations: serde_json::Value =
        serde_json::from_str(include_str!("../../app/config/operations.json"))?;
    let timeout = Duration::from_secs(
        operations["timeout_seconds"]
            .as_u64()
            .ok_or("invalid timeout")?,
    );
    render_with_timeout(runtime, source, style, output, timeout)
}

pub fn render_with_timeout(
    runtime: &Path,
    source: &Path,
    style: &Path,
    output: &Path,
    timeout: Duration,
) -> Result<(), Box<dyn Error>> {
    let runtime = fs::canonicalize(runtime)?;
    let operations: serde_json::Value =
        serde_json::from_str(include_str!("../../app/config/operations.json"))?;
    let request_limit = operations["request_body_bytes"]
        .as_u64()
        .ok_or("invalid request limit")?;
    let source = String::from_utf8(read_limited(source, request_limit)?)?;
    let style: serde_json::Value = serde_json::from_slice(&read_limited(style, request_limit)?)?;
    let request = serde_json::to_vec(&serde_json::json!({"source": source, "style": style}))?;
    if request.len() as u64
        > operations["request_body_bytes"]
            .as_u64()
            .ok_or("invalid request limit")?
    {
        return Err("request exceeds limit".into());
    }
    let work = tempfile::tempdir()?;
    fs::write(work.path().join("request.json"), request)?;
    let worker = runtime
        .join("worker")
        .join(format!("jjokgyeol-worker{}", std::env::consts::EXE_SUFFIX));
    let mut stderr = tempfile::tempfile()?;
    let status = run_worker(&worker, &runtime, work.path(), timeout, stderr.try_clone()?)?;
    if !status.success() {
        return Err(worker_error(&mut stderr).into());
    }
    let pdf = read_limited(
        &work.path().join("response.pdf"),
        operations["worker_file_bytes"]
            .as_u64()
            .ok_or("invalid worker file limit")?,
    )?;
    if !pdf.starts_with(b"%PDF-") || !pdf.trim_ascii_end().ends_with(b"%%EOF") {
        return Err("invalid PDF".into());
    }
    let parent = output
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let mut temp = tempfile::NamedTempFile::new_in(parent)?;
    temp.write_all(&pdf)?;
    temp.persist(output)?;
    Ok(())
}

fn worker_error(stderr: &mut fs::File) -> String {
    let diagnostic = (|| -> Option<String> {
        stderr.seek(SeekFrom::Start(0)).ok()?;
        let mut bytes = Vec::new();
        stderr
            .take(WORKER_ERROR_LIMIT + 1)
            .read_to_end(&mut bytes)
            .ok()?;
        if bytes.len() as u64 > WORKER_ERROR_LIMIT {
            return None;
        }
        let value: serde_json::Value = serde_json::from_slice(&bytes).ok()?;
        if value.get("code")?.as_str()? != "invalid_input" {
            return None;
        }
        let field = value.get("field")?.as_str()?;
        let message = value.get("message")?.as_str()?;
        if field.is_empty()
            || message.is_empty()
            || field.chars().chain(message.chars()).any(char::is_control)
        {
            return None;
        }
        Some(
            serde_json::json!({"code": "invalid_input", "field": field, "message": message})
                .to_string(),
        )
    })();
    diagnostic.unwrap_or_else(|| "worker failed".to_owned())
}

fn read_limited(path: &Path, limit: u64) -> Result<Vec<u8>, Box<dyn Error>> {
    let mut bytes = Vec::new();
    fs::File::open(path)?
        .take(limit.checked_add(1).ok_or("invalid file limit")?)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 > limit {
        return Err("file exceeds limit".into());
    }
    Ok(bytes)
}

#[cfg(unix)]
fn run_worker(
    worker: &Path,
    runtime: &Path,
    work: &Path,
    timeout: Duration,
    stderr: fs::File,
) -> Result<std::process::ExitStatus, Box<dyn Error>> {
    let deadline = Instant::now()
        .checked_add(timeout)
        .ok_or("invalid timeout")?;
    let mut child = Command::new(worker)
        .arg("--runtime")
        .arg(runtime)
        .current_dir(work)
        .stderr(stderr)
        .process_group(0)
        .spawn()?;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                unsafe {
                    libc::kill(-(child.id() as i32), libc::SIGKILL);
                }
                return Ok(status);
            }
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(10)),
            result => {
                unsafe {
                    libc::kill(-(child.id() as i32), libc::SIGKILL);
                }
                let _ = child.wait();
                return Err(match result {
                    Err(error) => error.into(),
                    _ => "worker timed out".into(),
                });
            }
        }
    }
}

#[cfg(windows)]
fn run_worker(
    worker: &Path,
    runtime: &Path,
    work: &Path,
    timeout: Duration,
    stderr: fs::File,
) -> Result<std::process::ExitStatus, Box<dyn Error>> {
    use windows_spawn::{Command, DropPolicy, SpawnOptions};

    let deadline = Instant::now()
        .checked_add(timeout)
        .ok_or("invalid timeout")?;
    let mut child = Command::new(worker)
        .arg("--runtime")
        .arg(runtime)
        .current_dir(work)
        .stderr(stderr)
        .spawn_with(SpawnOptions::new().drop_policy(DropPolicy::KillTree))?;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return Ok(status),
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(10)),
            Err(error) => return Err(error.into()),
            _ => return Err("worker timed out".into()),
        }
    }
}

#[cfg(not(any(unix, windows)))]
fn run_worker(
    _: &Path,
    _: &Path,
    _: &Path,
    _: Duration,
    _: fs::File,
) -> Result<std::process::ExitStatus, Box<dyn Error>> {
    Err("process-tree timeout is not implemented on this platform".into())
}
