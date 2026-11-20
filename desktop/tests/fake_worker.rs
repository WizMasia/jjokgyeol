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
