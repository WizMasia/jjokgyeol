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
fn renders_with_utf8_bom_in_style_file() {
    let (runtime, root) = setup();
    let style = root.path().join("양식.json");
    fs::write(&style, "\u{feff}{\"size\":65536}").unwrap();
    let output = root.path().join("결과.pdf");
    jjokgyeol_proof::render(&runtime, &root.path().join("원고.md"), &style, &output).unwrap();
    assert_eq!(fs::read(output).unwrap(), b"%PDF-1.4\n%%EOF\n");
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

#[test]
fn fixture_diagnostics_are_opt_in_and_bounded() {
    let (runtime, root) = setup();
    let source = root.path().join("원고.md");
    let style = root.path().join("양식.json");
    fs::write(
        &source,
        include_str!("../../proofs/numbering/manuscript.md")
            .replace("\r\n", "\n")
            .replace('\r', "\n")
            .replace("\n", "\r\n"),
    )
    .unwrap();
    fs::write(&style, include_str!("../../proofs/numbering/odd-even.json")).unwrap();
    let invoke = |enabled: bool| {
        let mut cmd = Command::new(env!("CARGO_BIN_EXE_jjokgyeol-proof"));
        cmd.arg("--runtime")
            .arg(&runtime)
            .arg("--source")
            .arg(&source)
            .arg("--style")
            .arg(&style)
            .arg("--output")
            .arg(root.path().join("결과.pdf"));
        if enabled {
            cmd.env("JJOKGYEOL_TEST_DIAGNOSTICS", "1");
        } else {
            cmd.env_remove("JJOKGYEOL_TEST_DIAGNOSTICS");
        }
        cmd.output().unwrap()
    };
    let disabled = invoke(false);
    assert_eq!(
        String::from_utf8_lossy(&disabled.stderr).trim(),
        "worker failed"
    );
    let enabled = invoke(true);
    let err = String::from_utf8_lossy(&enabled.stderr);
    assert!(err.contains("fixture worker diagnostic"), "{err}");
    assert!(err.contains("fixture exception"), "{err}");
    assert!(err.len() < 17 * 1024);
    fs::write(&source, "internal_error").unwrap();
    let private = invoke(true);
    assert_eq!(
        String::from_utf8_lossy(&private.stderr).trim(),
        "worker failed"
    );
    fs::write(
        &source,
        include_str!("../../proofs/numbering/manuscript.md"),
    )
    .unwrap();
    fs::write(&style, "{}").unwrap();
    let other_style = invoke(true);
    assert_eq!(
        String::from_utf8_lossy(&other_style.stderr).trim(),
        "worker failed"
    );
}

#[test]
fn worker_does_not_inherit_secret_env() {
    let (runtime, root) = setup();
    let output = root.path().join("결과.pdf");
    fs::write(root.path().join("원고.md"), "check_env").unwrap();
    let result = Command::new(env!("CARGO_BIN_EXE_jjokgyeol-proof"))
        .env("JJOKGYEOL_SECRET_LEAK", "top-secret-token")
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
    assert!(
        result.status.success(),
        "secret env leaked to worker: {}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert_eq!(fs::read(output).unwrap(), b"%PDF-1.4\n%%EOF\n");
}

#[test]
fn decision_worker_uses_private_stdin_and_returns_binary_comparison() {
    use std::time::Duration;
    let (runtime, _root) = setup();
    let connection = serde_json::json!({"provider":"chat","api_key":"test-only-secret"});
    let timeout = Duration::from_secs(5);
    jjokgyeol_proof::check_decision_connection(
        &runtime,
        connection.to_string().into_bytes(),
        timeout,
    )
    .unwrap();
    for source in ["model", "mixed", "rule"] {
        let request =
            serde_json::json!({"source":source,"style":{"size":65536},"connection":connection});
        let frame = jjokgyeol_proof::render_model_proof(
            &runtime,
            request.to_string().into_bytes(),
            timeout,
        )
        .unwrap();
        let size = u32::from_be_bytes(frame[..4].try_into().unwrap()) as usize;
        let header: serde_json::Value = serde_json::from_slice(&frame[4..4 + size]).unwrap();
        let rule = b"%PDF-1.4\nrule\n%%EOF\n";
        let reason = match source {
            "mixed" => Some("rate_limit"),
            "rule" => Some("authentication"),
            _ => None,
        };
        assert_eq!(
            header,
            serde_json::json!({"decision":source,"rule_bytes":rule.len(),"reason":reason})
        );
        assert_eq!(&frame[4 + size..4 + size + rule.len()], rule);
        let extra: &[u8] = if source == "rule" {
            b""
        } else {
            b"%PDF-1.4\nselected\n%%EOF\n"
        };
        assert_eq!(&frame[4 + size + rule.len()..], extra);
    }
}

#[test]
fn catalog_worker_uses_private_stdin_and_returns_only_model_lists() {
    let (runtime, _root) = setup();
    let list = |scenario| {
        let request = serde_json::json!({"provider":"chat","base_url":format!("https://{scenario}.test/v1"),
                                        "api_key":"test-only-secret"});
        jjokgyeol_proof::list_decision_models(
            &runtime,
            request.to_string().into_bytes(),
            std::time::Duration::from_secs(5),
        )
    };
    assert_eq!(
        list("valid").unwrap(),
        serde_json::json!({"models":["모델/a","z"]})
    );
    assert_eq!(list("empty").unwrap(), serde_json::json!({"models":[]}));
    for (scenario, expected) in [
        ("denied", "authentication"),
        ("private-error", "unavailable"),
        ("malformed", "invalid_response"),
        ("oversized", "invalid_response"),
    ] {
        assert_eq!(
            list(scenario).unwrap_err(),
            serde_json::json!({"code":expected}).to_string()
        );
    }
}

#[test]
fn failed_check_returns_only_a_public_code() {
    let (runtime, _root) = setup();
    for (scenario, expected) in [("reject", "authentication"), ("private", "unavailable")] {
        let request = serde_json::json!({"model":scenario, "api_key":"test-only-secret"});
        let error = jjokgyeol_proof::check_decision_connection(
            &runtime,
            request.to_string().into_bytes(),
            std::time::Duration::from_secs(5),
        )
        .unwrap_err();
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(&error).unwrap(),
            serde_json::json!({"code":expected})
        );
    }
}

#[test]
fn decision_timeout_while_writing_stdin_is_bounded() {
    let (runtime, _root) = setup();
    fs::write(runtime.join("block-stdin"), "").unwrap();
    let start = std::time::Instant::now();
    let error = jjokgyeol_proof::check_decision_connection(
        &runtime,
        vec![b'x'; 900_000],
        std::time::Duration::from_millis(400),
    )
    .unwrap_err();
    assert_eq!(
        serde_json::from_str::<serde_json::Value>(&error).unwrap()["code"],
        "timeout"
    );
    assert!(start.elapsed() < std::time::Duration::from_secs(3));
}

#[test]
fn hung_replay_returns_only_committed_baseline_within_decision_budget() {
    let (runtime, root) = setup();
    let source = serde_json::json!({"source":"hang", "connection":{"api_key":"test-only-secret"}});
    let start = std::time::Instant::now();
    let frame = jjokgyeol_proof::render_model_proof(
        &runtime,
        source.to_string().into_bytes(),
        std::time::Duration::from_secs(30),
    )
    .unwrap();
    assert!(start.elapsed() < std::time::Duration::from_secs(20));
    let size = u32::from_be_bytes(frame[..4].try_into().unwrap()) as usize;
    let header: serde_json::Value = serde_json::from_slice(&frame[4..4 + size]).unwrap();
    assert_eq!(header["decision"], "rule");
    assert_eq!(header["reason"], "timeout");
    assert_eq!(&frame[4 + size..], b"%PDF-1.4\nrule\n%%EOF\n");
    std::thread::sleep(std::time::Duration::from_millis(2100));
    assert!(!root.path().join("descendant-survived").exists());
}

#[test]
fn unpublished_measurement_cannot_be_delivered() {
    let (runtime, _root) = setup();
    let source =
        serde_json::json!({"source":"incomplete", "connection":{"api_key":"test-only-secret"}});
    assert!(jjokgyeol_proof::render_model_proof(
        &runtime,
        source.to_string().into_bytes(),
        std::time::Duration::from_millis(400)
    )
    .is_err());
}

#[test]
fn expired_decision_reports_timeout_before_opening_runtime() {
    let root = tempfile::tempdir().unwrap();
    let missing = root.path().join("missing");
    let zero = std::time::Duration::ZERO;
    let errors = [
        jjokgyeol_proof::check_decision_connection(&missing, b"{}".to_vec(), zero).unwrap_err(),
        jjokgyeol_proof::list_decision_models(&missing, b"{}".to_vec(), zero).unwrap_err(),
        jjokgyeol_proof::render_model_proof(&missing, b"{}".to_vec(), zero).unwrap_err(),
    ];
    for error in errors {
        assert_eq!(error, r#"{"code":"timeout"}"#);
    }
}

#[test]
fn binary_proof_exceeds_legacy_request_limit_in_isolated_worker() {
    let (runtime, _root) = setup();
    let header = br#"{"request":{"source":"test-only-secret","style":{},"connection":{"provider":"rule"}},"faces":[{"hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","size":2000000}]}"#;
    let mut body = (header.len() as u32).to_be_bytes().to_vec();
    body.extend_from_slice(header);
    body.resize(body.len() + 2_000_000, 0xff);
    let proof =
        jjokgyeol_proof::render_model_proof(&runtime, body, std::time::Duration::from_secs(5))
            .unwrap();
    assert!(proof.ends_with(b"%PDF-1.4\nrule\n%%EOF\n"));
}

#[test]
fn optional_spacing_matches_rule_and_selected_pdfs_and_excludes_private_fields() {
    let (runtime, _root) = setup();
    for (source, plain) in [("plain-rule", true), ("mixed", false), ("rule", false)] {
        let request = serde_json::json!({"source":source,"style":{},"secret":"test-only-secret"});
        let frame = jjokgyeol_proof::render_preview(
            &runtime,
            request.to_string().into_bytes(),
            std::time::Duration::from_secs(5),
            plain,
            true,
        )
        .unwrap();
        let end = 4 + u32::from_be_bytes(frame[..4].try_into().unwrap()) as usize;
        let header: serde_json::Value = serde_json::from_slice(&frame[4..end]).unwrap();
        let rule = serde_json::json!([{"before_line":0,"after_line":2,"gap_sp":0,"source":"rule"}]);
        let selected =
            serde_json::json!([{"before_line":0,"after_line":2,"gap_sp":360448,"source":"ai"}]);
        assert_eq!(
            header["spacing"],
            if source == "mixed" {
                serde_json::json!({"rule":rule,"model":selected})
            } else {
                serde_json::json!({"rule":rule})
            }
        );
        assert!(frame[end..].starts_with(b"%PDF-1.4\nrule\n%%EOF\n"));
    }
}

#[test]
fn failed_spacing_worker_returns_committed_line_replay_with_its_receipt() {
    let (runtime, _root) = setup();
    let request = br#"{"source":"replay-crash","key":"test-only-secret"}"#.to_vec();
    let frame = jjokgyeol_proof::render_preview(
        &runtime,
        request,
        std::time::Duration::from_secs(5),
        false,
        true,
    )
    .unwrap();
    let end = 4 + u32::from_be_bytes(frame[..4].try_into().unwrap()) as usize;
    let header: serde_json::Value = serde_json::from_slice(&frame[4..end]).unwrap();
    assert_eq!(header["decision"], "mixed");
    assert_eq!(header["reason"], "invalid_response");
    assert_eq!(header["spacing"]["model"][0]["gap_sp"], 0);
    assert!(frame[end..].ends_with(b"%PDF-line\n%%EOF"));
}
