use super::{
    operations, read_limited, read_pdf, spawn_worker, validation_error, wait_worker, Stdio,
    WORKER_ERROR_LIMIT,
};
use serde_json::{json, Value};
use std::{
    fs,
    io::{Read, Write},
    path::Path,
    thread,
    time::{Duration, Instant},
};

fn error(code: &str) -> String {
    json!({"code":code}).to_string()
}

fn public_code(value: &Value) -> Option<&str> {
    value.as_str().filter(|code| {
        matches!(
            *code,
            "invalid_connection"
                | "address"
                | "authentication"
                | "model"
                | "rate_limit"
                | "timeout"
                | "builtin_unavailable"
                | "unavailable"
                | "invalid_response"
        )
    })
}

fn config_number(config: &Value, key: &str) -> u64 {
    config[key].as_u64().expect("invalid bundled limit")
}

struct ModelBudget {
    duration: Duration,
    reserve: Duration,
    end: Option<Instant>,
}

impl ModelBudget {
    fn configured() -> Self {
        let config: Value =
            serde_json::from_str(include_str!("../../app/config/decision.json")).unwrap();
        Self {
            duration: Duration::from_secs_f64(config["decision_seconds"].as_f64().unwrap()),
            reserve: Duration::from_secs_f64(config["delivery_reserve_seconds"].as_f64().unwrap()),
            end: None,
        }
    }

    fn check(&mut self, marker: &Path, deadline: Instant) -> Result<(), String> {
        let now = Instant::now();
        if self.end.is_none() && marker.is_file() {
            self.end = Some((now + self.duration).min(deadline - self.reserve));
        }
        if self.end.is_some_and(|end| now >= end) {
            return Err("worker timed out".into());
        }
        Ok(())
    }
}

fn run(
    runtime: &Path,
    work: &Path,
    flag: &str,
    request: Vec<u8>,
    deadline: Instant,
    mut budget: Option<ModelBudget>,
) -> Result<(), String> {
    let config = operations().map_err(|_| error("unavailable"))?;
    let request_limit = config_number(&config, "request_body_bytes");
    if flag == "--model-proof" && request.first() == Some(&0) {
        if !super::DocumentFontLimits::configured().accepts_frame(&request, request_limit) {
            return Err(json!({"code":"invalid_input","field":"fonts","message":"글꼴 요청 크기를 확인하세요."}).to_string());
        }
    } else if request.len() as u64 > request_limit {
        return Err(error("invalid_connection"));
    }
    let runtime = fs::canonicalize(runtime).map_err(|_| error("unavailable"))?;
    if Instant::now() >= deadline {
        return Err(error("timeout"));
    }
    let mut child = spawn_worker(&runtime, work, flag, Stdio::piped(), Stdio::piped())
        .map_err(|_| error("unavailable"))?;
    let mut stdin = child.stdin.take().unwrap();
    let stderr = child.stderr.take().unwrap();
    let marker = work.join("baseline-ready");
    // A stalled worker can block the pipe writer; the supervising thread must stay free to kill it.
    let (exit, diagnostic, sent) = thread::scope(|scope| {
        let writer = scope.spawn(move || {
            if Instant::now() >= deadline {
                return Err(error("timeout"));
            }
            stdin.write_all(&request).map_err(|_| error("unavailable"))
        });
        let reader = scope.spawn(move || {
            let mut bytes = Vec::new();
            stderr
                .take(WORKER_ERROR_LIMIT + 1)
                .read_to_end(&mut bytes)?;
            Ok::<_, std::io::Error>(bytes)
        });
        let exit = wait_worker(
            &mut child,
            deadline.saturating_duration_since(Instant::now()),
            || {
                if Instant::now() >= deadline {
                    return Err("worker timed out".into());
                }
                if let Some(budget) = &mut budget {
                    budget.check(&marker, deadline)?;
                }
                Ok(())
            },
        );
        // On Windows closing the job kills descendants retaining either pipe.
        drop(child);
        (exit, reader.join().unwrap(), writer.join().unwrap())
    });
    let status = exit.map_err(|cause| {
        error(if cause == "worker timed out" {
            "timeout"
        } else {
            "unavailable"
        })
    })?;
    if !status.success() {
        let bytes = diagnostic.unwrap_or_default();
        if flag == "--model-proof" {
            if let Some(message) = validation_error(&bytes) {
                return Err(message);
            }
        }
        let data = serde_json::from_slice::<Value>(&bytes).unwrap_or(Value::Null);
        return Err(error(public_code(&data["code"]).unwrap_or("unavailable")));
    }
    sent
}

fn deadline_after(timeout: Duration) -> Result<Instant, String> {
    if timeout.is_zero() {
        return Err(error("timeout"));
    }
    Instant::now()
        .checked_add(timeout)
        .ok_or_else(|| error("timeout"))
}

pub fn decision_check_timeout(timeout: Duration) -> Duration {
    timeout.min(ModelBudget::configured().duration)
}

pub fn check_decision_connection(
    runtime: &Path,
    connection: Vec<u8>,
    timeout: Duration,
) -> Result<(), String> {
    let deadline = deadline_after(decision_check_timeout(timeout))?;
    let work = tempfile::tempdir().map_err(|_| error("unavailable"))?;
    run(
        runtime,
        work.path(),
        "--check-decision",
        connection,
        deadline,
        None,
    )
}

pub fn render_model_proof(
    runtime: &Path,
    request: Vec<u8>,
    timeout: Duration,
) -> Result<Vec<u8>, String> {
    let deadline = deadline_after(timeout)?;
    let work = tempfile::tempdir().map_err(|_| error("unavailable"))?;
    let path = work.path();
    let result = run(
        runtime,
        path,
        "--model-proof",
        request,
        deadline,
        Some(ModelBudget::configured()),
    );
    if !path.join("baseline-ready").is_file() {
        return Err(result.err().unwrap_or_else(|| error("invalid_response")));
    }
    let config = operations().map_err(|_| error("unavailable"))?;
    let limit = config_number(&config, "worker_file_bytes");
    let rule =
        read_pdf(&path.join("measure/book.pdf"), limit).map_err(|_| error("invalid_response"))?;
    let selection = || -> Option<(String, Value, Vec<u8>)> {
        let meta = read_limited(&path.join("decision.json"), WORKER_ERROR_LIMIT).ok()?;
        let meta: Value = serde_json::from_slice(&meta).ok()?;
        let decision = meta["decision"].as_str()?;
        let reason = meta.get("reason")?;
        if !(reason.is_null() || public_code(reason).is_some()) {
            return None;
        }
        let selected = match decision {
            "rule" => vec![],
            "model" | "mixed" => read_pdf(&path.join("response.pdf"), limit).ok()?,
            _ => return None,
        };
        Some((decision.to_owned(), reason.clone(), selected))
    };
    let (decision, reason, selected) = match result {
        Ok(()) => selection().unwrap_or_else(|| ("rule".into(), json!("invalid_response"), vec![])),
        Err(cause) => {
            let cause: Value = serde_json::from_str(&cause).unwrap_or(Value::Null);
            (
                "rule".into(),
                json!(public_code(&cause["code"]).unwrap_or("unavailable")),
                vec![],
            )
        }
    };
    let header = json!({"decision":decision, "reason":reason, "rule_bytes":rule.len()}).to_string();
    let mut frame = Vec::with_capacity(4 + header.len() + rule.len() + selected.len());
    frame.extend_from_slice(&(header.len() as u32).to_be_bytes());
    frame.extend_from_slice(header.as_bytes());
    frame.extend_from_slice(&rule);
    frame.extend_from_slice(&selected);
    Ok(frame)
}
