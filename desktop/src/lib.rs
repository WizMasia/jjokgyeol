#[cfg(unix)]
use std::os::unix::process::CommandExt;
#[cfg(unix)]
use std::process::Command;
use std::{
    error::Error,
    ffi::OsString,
    fs,
    io::{Read, Seek, SeekFrom, Write},
    path::Path,
    time::{Duration, Instant},
};

const WORKER_ERROR_LIMIT: u64 = 16 * 1024;
// 워커 stderr 디스크 고갈 방지 상한 (4 MiB) / Cap live worker stderr file size (4 MiB).
const WORKER_STDERR_MONITOR_LIMIT: u64 = 4 * 1024 * 1024;

/// 워커 프로세스에 전달할 최소 OS 환경변수 화이트리스트를 구성합니다.
/// Build the minimal allowlisted environment variable pairs for worker processes.
pub fn worker_env_pairs() -> Vec<(OsString, OsString)> {
    #[cfg(unix)]
    const BASE_KEYS: &[&str] = &["PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR"];
    #[cfg(windows)]
    const BASE_KEYS: &[&str] = &[
        "PATH",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
    ];
    #[cfg(not(any(unix, windows)))]
    const BASE_KEYS: &[&str] = &["PATH"];

    let mut pairs = Vec::new();
    for key in BASE_KEYS {
        if let Some(val) = std::env::var_os(key) {
            pairs.push((OsString::from(key), val));
        }
    }
    if let Some(val) = std::env::var_os("TEXMFCACHE") {
        pairs.push((OsString::from("TEXMFCACHE"), val));
    }
    if std::env::var_os("JJOKGYEOL_TEST_DIAGNOSTICS").as_deref() == Some(std::ffi::OsStr::new("1"))
    {
        pairs.push((
            OsString::from("JJOKGYEOL_TEST_DIAGNOSTICS"),
            OsString::from("1"),
        ));
    }
    pairs
}

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
    let bytes = read_limited(style, request_limit)?;
    let style: serde_json::Value =
        serde_json::from_slice(bytes.strip_prefix(b"\xEF\xBB\xBF").unwrap_or(&bytes))?;
    let qa_diagnostics = std::env::var_os("JJOKGYEOL_TEST_DIAGNOSTICS").as_deref()
        == Some(std::ffi::OsStr::new("1"))
        && source.replace("\r\n", "\n").replace('\r', "\n")
            == include_str!("../../proofs/numbering/manuscript.md")
                .replace("\r\n", "\n")
                .replace('\r', "\n")
        && style
            == serde_json::from_str::<serde_json::Value>(include_str!(
                "../../proofs/numbering/odd-even.json"
            ))?;
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
        if qa_diagnostics {
            emit_qa_diagnostics(&mut stderr, work.path(), status);
        }
        return Err(sanitize_worker_error(&mut stderr).into());
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

fn emit_qa_diagnostics(stderr: &mut fs::File, work: &Path, status: std::process::ExitStatus) {
    eprintln!("QA worker exit: {status}");
    for (label, mut file) in [
        (
            "exception",
            fs::File::open(work.join("diagnostic-exception.txt")),
        ),
        ("worker stderr", stderr.try_clone()),
        ("TeX log", fs::File::open(work.join("diagnostic-book.log"))),
    ] {
        if let Ok(ref mut file) = file {
            let _ = file.seek(SeekFrom::Start(0));
            let mut bytes = Vec::new();
            if file.take(4096).read_to_end(&mut bytes).is_ok() && !bytes.is_empty() {
                let text = String::from_utf8_lossy(&bytes);
                let mut end = text.len().min(4096);
                while !text.is_char_boundary(end) {
                    end -= 1;
                }
                eprintln!("QA {label} (up to 4096 bytes): {}", &text[..end]);
            }
        }
    }
}

/// 워커 stderr에서 검증된 공개 invalid_input JSON만 추출합니다.
/// Extract only validated public invalid_input JSON from worker stderr.
pub fn sanitize_worker_error(stderr: &mut fs::File) -> String {
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

/// Unix에서 PID 재사용 경쟁 조건 없이 자식 프로세스 그룹을 감시·종료합니다.
/// Supervise and terminate a Unix process group without PID-reuse race using waitid(WNOWAIT).
#[cfg(unix)]
pub fn wait_unix_process_group<F>(
    child: &mut std::process::Child,
    timeout: Duration,
    mut check_limits: F,
) -> Result<std::process::ExitStatus, String>
where
    F: FnMut() -> Result<(), String>,
{
    let deadline = Instant::now()
        .checked_add(timeout)
        .ok_or_else(|| "invalid timeout".to_owned())?;
    let pgid = i32::try_from(child.id()).map_err(|_| "invalid worker pid".to_owned())?;
    loop {
        if let Err(err) = check_limits() {
            unsafe {
                libc::kill(-pgid, libc::SIGKILL);
            }
            let _ = child.wait();
            return Err(err);
        }
        let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
        let rc = unsafe {
            libc::waitid(
                libc::P_PID,
                child.id() as libc::id_t,
                &mut info,
                libc::WEXITED | libc::WNOWAIT | libc::WNOHANG,
            )
        };
        if rc == 0 {
            if info.si_signo != 0 {
                // 자식이 좀비 상태로 남아 PID가 고정된 시점에 그룹 전체를 정리한 뒤 회수한다.
                // Child is still a zombie pinning its PGID; kill remaining descendants before reaping.
                unsafe {
                    libc::kill(-pgid, libc::SIGKILL);
                }
                return child.wait().map_err(|e| e.to_string());
            }
            if Instant::now() < deadline {
                std::thread::sleep(Duration::from_millis(10));
            } else {
                unsafe {
                    libc::kill(-pgid, libc::SIGKILL);
                }
                let _ = child.wait();
                return Err("worker timed out".to_owned());
            }
        } else {
            let err = std::io::Error::last_os_error();
            if err.kind() == std::io::ErrorKind::Interrupted {
                continue;
            }
            unsafe {
                libc::kill(-pgid, libc::SIGKILL);
            }
            let _ = child.wait();
            return Err(err.to_string());
        }
    }
}

#[cfg(unix)]
fn run_worker(
    worker: &Path,
    runtime: &Path,
    work: &Path,
    timeout: Duration,
    stderr: fs::File,
) -> Result<std::process::ExitStatus, Box<dyn Error>> {
    let stderr_monitor = stderr.try_clone()?;
    let mut command = Command::new(worker);
    command
        .env_clear()
        .envs(worker_env_pairs())
        .arg("--runtime")
        .arg(runtime)
        .current_dir(work)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(stderr)
        .process_group(0);
    let mut child = command.spawn()?;
    wait_unix_process_group(&mut child, timeout, || {
        if stderr_monitor
            .metadata()
            .map(|m| m.len() > WORKER_STDERR_MONITOR_LIMIT)
            .unwrap_or(false)
        {
            return Err("worker failed".to_owned());
        }
        Ok(())
    })
    .map_err(Into::into)
}

#[cfg(windows)]
fn run_worker(
    worker: &Path,
    runtime: &Path,
    work: &Path,
    timeout: Duration,
    stderr: fs::File,
) -> Result<std::process::ExitStatus, Box<dyn Error>> {
    use windows_spawn::{Command, DropPolicy, SpawnOptions, Stdio};

    let deadline = Instant::now()
        .checked_add(timeout)
        .ok_or("invalid timeout")?;
    let stderr_monitor = stderr.try_clone()?;
    let mut command = Command::new(worker);
    command
        .env_clear()
        .envs(worker_env_pairs())
        .arg("--runtime")
        .arg(runtime)
        .current_dir(work)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(stderr);
    let mut child = command.spawn_with(SpawnOptions::new().drop_policy(DropPolicy::KillTree))?;
    loop {
        if stderr_monitor
            .metadata()
            .map(|m| m.len() > WORKER_STDERR_MONITOR_LIMIT)
            .unwrap_or(false)
        {
            return Err("worker failed".into());
        }
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
