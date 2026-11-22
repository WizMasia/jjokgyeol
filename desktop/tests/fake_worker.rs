use std::{env, fs, process::Command, thread, time::Duration};
fn main() {
    let args: Vec<_> = env::args_os().collect();
    if args[1] == "--child" {
        thread::sleep(Duration::from_secs(2));
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
        Command::new(env::current_exe().unwrap())
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
