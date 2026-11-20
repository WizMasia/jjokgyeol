use std::{fs, path::PathBuf, process::Command};

fn setup() -> (PathBuf, tempfile::TempDir) {
    let root = tempfile::tempdir().unwrap();
    let runtime = root.path().join("런타임 space");
    fs::create_dir_all(runtime.join("worker")).unwrap();
    let status = Command::new("rustc")
        .args(["--edition=2021", "tests/fake_worker.rs", "-o"])
        .arg(runtime.join("worker/jjokgyeol-worker"))
        .status()
        .unwrap();
    assert!(status.success());
    fs::write(root.path().join("원고.md"), "안녕하세요").unwrap();
    fs::write(root.path().join("양식.json"), "{\"size\":65536}").unwrap();
    (runtime, root)
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
            fs::remove_file(runtime.join("worker/jjokgyeol-worker")).unwrap();
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
