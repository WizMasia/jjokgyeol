use std::{fs, path::PathBuf, process::Command};

fn setup() -> (PathBuf, tempfile::TempDir) {
    let root = tempfile::tempdir().unwrap();
    let runtime = root.path().join("런타임 space");
    fs::create_dir_all(runtime.join("worker")).unwrap();
    let status = Command::new("rustc")
        .args(["--edition=2021", "tests/fake_worker.rs", "-o"])
        .arg(worker_path(&runtime))
        .status()
        .unwrap();
    assert!(status.success());
    fs::write(root.path().join("원고.md"), "안녕하세요").unwrap();
    fs::write(root.path().join("양식.json"), "{\"size\":65536}").unwrap();
    (runtime, root)
}

fn worker_path(runtime: &std::path::Path) -> PathBuf {
    runtime
        .join("worker")
        .join(format!("jjokgyeol-worker{}", std::env::consts::EXE_SUFFIX))
}

#[test]
fn exposes_only_public_validation_error_fields() {
    let (runtime, root) = setup();
    let output = root.path().join("결과.pdf");
    fs::write(&output, b"previous pdf").unwrap();
    fs::write(root.path().join("원고.md"), "validation_error").unwrap();
    let error = jjokgyeol_proof::render_with_timeout(
        &runtime,
        &root.path().join("원고.md"),
        &root.path().join("양식.json"),
        &output,
        std::time::Duration::from_secs(5),
    )
    .unwrap_err()
    .to_string();
    assert_eq!(
        serde_json::from_str::<serde_json::Value>(&error).unwrap(),
        serde_json::json!({"code":"invalid_input", "field":"body_sp", "message":"글자 크기를 확인하세요."})
    );
    assert_eq!(fs::read(output).unwrap(), b"previous pdf");
    let result = Command::new(env!("CARGO_BIN_EXE_jjokgyeol-proof"))
        .arg("--runtime")
        .arg(&runtime)
        .arg("--source")
        .arg(root.path().join("원고.md"))
        .arg("--style")
        .arg(root.path().join("양식.json"))
        .arg("--output")
        .arg(root.path().join("결과.pdf"))
        .output()
        .unwrap();
    assert!(!result.status.success());
    assert_eq!(
        serde_json::from_slice::<serde_json::Value>(&result.stderr).unwrap(),
        serde_json::json!({"code":"invalid_input", "field":"body_sp", "message":"글자 크기를 확인하세요."})
    );
}

#[test]
fn hides_internal_malformed_and_large_stderr() {
    let (runtime, root) = setup();
    for source in [
        "internal_error",
        "malformed_error",
        "invalid_schema",
        "large_error",
    ] {
        fs::write(root.path().join("원고.md"), source).unwrap();
        let start = std::time::Instant::now();
        let error = jjokgyeol_proof::render_with_timeout(
            &runtime,
            &root.path().join("원고.md"),
            &root.path().join("양식.json"),
            &root.path().join("결과.pdf"),
            std::time::Duration::from_secs(3),
        )
        .unwrap_err()
        .to_string();
        assert_eq!(error, "worker failed", "{source}");
        assert!(start.elapsed() < std::time::Duration::from_secs(3));
    }
}

#[test]
fn writes_pdf_for_unicode_paths() {
    let (runtime, root) = setup();
    let output = root.path().join("결과.pdf");
    let result = Command::new(env!("CARGO_BIN_EXE_jjokgyeol-proof"))
        .arg("--runtime")
        .arg(runtime)
        .arg("--source")
        .arg(root.path().join("원고.md"))
        .arg("--style")
        .arg(root.path().join("양식.json"))
        .arg("--output")
        .arg(&output)
        .output()
        .unwrap();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert_eq!(fs::read(output).unwrap(), b"%PDF-1.4\n%%EOF\n");
}

#[test]
fn preserves_existing_output_on_failure_or_truncated_pdf() {
    let (runtime, root) = setup();
    let output = root.path().join("결과.pdf");
    for source in ["failure", "truncated", "oversize", "missing"] {
        fs::write(&output, b"previous pdf").unwrap();
        fs::write(root.path().join("원고.md"), source).unwrap();
        if source == "missing" {
            fs::remove_file(worker_path(&runtime)).unwrap();
        }
        let result = Command::new(env!("CARGO_BIN_EXE_jjokgyeol-proof"))
            .arg("--runtime")
            .arg(&runtime)
            .arg("--source")
            .arg(root.path().join("원고.md"))
            .arg("--style")
            .arg(root.path().join("양식.json"))
            .arg("--output")
            .arg(&output)
            .output()
            .unwrap();
        assert!(!result.status.success());
        assert_eq!(fs::read(&output).unwrap(), b"previous pdf");
    }
}

#[test]
fn successful_worker_does_not_leave_descendants() {
    let (runtime, root) = setup();
    fs::write(root.path().join("원고.md"), "descendant").unwrap();
    jjokgyeol_proof::render_with_timeout(
        &runtime,
        &root.path().join("원고.md"),
        &root.path().join("양식.json"),
        &root.path().join("결과.pdf"),
        std::time::Duration::from_secs(5),
    )
    .unwrap();
    std::thread::sleep(std::time::Duration::from_millis(2200));
    assert!(!root.path().join("descendant-survived").exists());
}

#[test]
fn replaces_existing_output_on_success() {
    let (runtime, root) = setup();
    let output = root.path().join("결과.pdf");
    fs::write(&output, b"previous pdf").unwrap();
    let result = Command::new(env!("CARGO_BIN_EXE_jjokgyeol-proof"))
        .arg("--runtime")
        .arg(&runtime)
        .arg("--source")
        .arg(root.path().join("원고.md"))
        .arg("--style")
        .arg(root.path().join("양식.json"))
        .arg("--output")
        .arg(&output)
        .output()
        .unwrap();
    assert!(result.status.success());
    assert_eq!(fs::read(output).unwrap(), b"%PDF-1.4\n%%EOF\n");
}

#[test]
fn timeout_terminates_descendants_and_preserves_output() {
    use std::time::{Duration, Instant};
    let (runtime, root) = setup();
    let output = root.path().join("결과.pdf");
    fs::write(&output, b"previous pdf").unwrap();
    fs::write(root.path().join("원고.md"), "timeout").unwrap();
    let start = Instant::now();
    let result = jjokgyeol_proof::render_with_timeout(
        &runtime,
        &root.path().join("원고.md"),
        &root.path().join("양식.json"),
        &output,
        Duration::from_millis(200),
    );
    assert!(result.is_err());
    assert!(
        start.elapsed() < Duration::from_secs(2),
        "worker was not timed out"
    );
    std::thread::sleep(Duration::from_millis(2200));
    assert!(!root.path().join("descendant-survived").exists());
    assert_eq!(fs::read(&output).unwrap(), b"previous pdf");
}
