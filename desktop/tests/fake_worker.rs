use std::{env, fs, process::Command, thread, time::Duration};
fn main() {
    let args: Vec<_> = env::args_os().collect();
    if args[1] == "--child" {
        thread::sleep(Duration::from_secs(2));
        fs::write(&args[2], "survived").unwrap();
        return;
    }
    if args[1] == "--model-proof" || args[1] == "--check-decision" {
        use std::io::Read;
        if env::current_exe()
            .unwrap()
            .parent()
            .unwrap()
            .parent()
            .unwrap()
            .join("block-stdin")
            .exists()
        {
            thread::sleep(Duration::from_secs(6));
        }
        let mut request = String::new();
        std::io::stdin().read_to_string(&mut request).unwrap();
        assert!(request.contains("test-only-secret"));
        assert!(args
            .iter()
            .all(|arg| !arg.to_string_lossy().contains("test-only-secret")));
        assert!(env::vars_os().all(|(_, val)| !val.to_string_lossy().contains("test-only-secret")));
        assert_eq!(fs::read_dir(".").unwrap().count(), 0);
        if args[1] == "--check-decision" {
            if request.contains("reject") {
                eprintln!(r#"{{"code":"authentication","message":"test-only-secret"}}"#);
                std::process::exit(1);
            }
            if request.contains("private") {
                eprintln!(r#"{{"code":"test-only-secret"}}"#);
                std::process::exit(1);
            }
            println!(r#"{{"ok":true}}"#);
            return;
        }
        fs::create_dir("measure").unwrap();
        fs::write("measure/book.pdf", b"%PDF-1.4\nrule\n%%EOF\n").unwrap();
        if request.contains("incomplete") {
            thread::sleep(Duration::from_secs(4));
        }
        fs::write("baseline-ready", "").unwrap();
        if request.contains("hang") {
            let marker = std::path::Path::new(&args[2])
                .parent()
                .unwrap()
                .join("descendant-survived");
            #[allow(clippy::zombie_processes)]
            let _child = Command::new(env::current_exe().unwrap())
                .arg("--late-child")
                .arg(marker)
                .spawn()
                .unwrap();
            thread::sleep(Duration::from_secs(30));
        }
        let status = if request.contains("mixed") {
            "mixed"
        } else if request.contains("rule") {
            "rule"
        } else {
            "model"
        };
        let reason = if status == "model" {
            "null"
        } else if status == "mixed" {
            r#""rate_limit""#
        } else {
            r#""authentication""#
        };
        fs::write(
            "decision.json",
            format!(r#"{{"decision":"{status}","reason":{reason}}}"#),
        )
        .unwrap();
        if status != "rule" {
            fs::write("response.pdf", b"%PDF-1.4\nselected\n%%EOF\n").unwrap();
        }
        return;
    }
    if args[1] == "--late-child" {
        thread::sleep(Duration::from_secs(17));
        fs::write(&args[2], "survived").unwrap();
        return;
    }
    assert_eq!(args[1], "--runtime");
    let request = fs::read_to_string("request.json").unwrap();
    if request.contains("교정용 현장 조사 보고서") {
        use std::io::Write;
        fs::write("diagnostic-exception.txt", "fixture exception").unwrap();
        fs::write("diagnostic-book.log", vec![b'x'; 100_000]).unwrap();
        let mut stderr = std::io::stderr().lock();
        stderr.write_all(b"fixture worker diagnostic").unwrap();
        stderr.write_all(&vec![b'x'; 100_000]).unwrap();
        std::process::exit(1);
    }
    if request.contains("validation_error") {
        eprintln!(
            r#"{{"code":"invalid_input","field":"body_sp","message":"글자 크기를 확인하세요.","secret":"private detail"}}"#
        );
        std::process::exit(1);
    }
    if request.contains("internal_error") {
        eprintln!(r#"{{"code":"worker_error","field":"source","message":"private detail"}}"#);
        std::process::exit(1);
    }
    if request.contains("malformed_error") {
        eprintln!("private traceback");
        std::process::exit(1);
    }
    if request.contains("invalid_schema") {
        eprintln!(r#"{{"code":"invalid_input","field":42,"message":"private detail"}}"#);
        std::process::exit(1);
    }
    if request.contains("large_error") {
        use std::io::Write;
        let mut stderr = std::io::stderr().lock();
        stderr
            .write_all(br#"{"code":"invalid_input","field":"source","message":""#)
            .unwrap();
        stderr.write_all(&vec![b'x'; 1_000_000]).unwrap();
        stderr.write_all(br#""}"#).unwrap();
        std::process::exit(1);
    }
    if request.contains("check_env") {
        if env::var_os("JJOKGYEOL_SECRET_LEAK").is_some() {
            eprintln!(r#"{{"code":"invalid_input","field":"env","message":"secret leaked"}}"#);
            std::process::exit(1);
        }
        fs::write("response.pdf", b"%PDF-1.4\n%%EOF\n").unwrap();
        return;
    }
    if request.contains("timeout") || request.contains("descendant") {
        let marker = std::path::Path::new(&args[2])
            .parent()
            .unwrap()
            .join("descendant-survived");
        // 프로세스 그룹 종료 테스트를 위해 의도적으로 대기하지 않고 자식 프로세스를 남깁니다.
        // Intentionally leave child unwaited to verify process-group cleanup on exit/timeout.
        #[allow(clippy::zombie_processes)]
        let _child = Command::new(env::current_exe().unwrap())
            .arg("--child")
            .arg(marker)
            .spawn()
            .unwrap();
        if request.contains("timeout") {
            thread::sleep(Duration::from_secs(3));
            return;
        }
    }
    if request.contains("oversize") {
        use std::io::{Seek, SeekFrom, Write};
        let mut file = fs::File::create("response.pdf").unwrap();
        file.write_all(b"%PDF-1.4\n").unwrap();
        file.seek(SeekFrom::Start(67_108_864)).unwrap();
        file.write_all(b"%%EOF\n").unwrap();
        return;
    }
    if request.contains("failure") {
        std::process::exit(2);
    }
    if request.contains("truncated") {
        fs::write("response.pdf", b"%PDF-1.4\n").unwrap();
        return;
    }
    assert!(request.contains("안녕하세요") || request.contains("descendant"));
    assert!(request.contains("65536"));
    fs::write("response.pdf", b"%PDF-1.4\n%%EOF\n").unwrap();
}
