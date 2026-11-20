use std::{ffi::OsString, path::PathBuf};
fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args_os().skip(1);
    let mut runtime = None;
    let mut source = None;
    let mut style = None;
    let mut output = None;
    while let Some(flag) = args.next() {
        let value = args.next().ok_or("missing argument value")?;
        let slot: &mut Option<OsString> = match flag.to_str() {
            Some("--runtime") => &mut runtime,
            Some("--source") => &mut source,
            Some("--style") => &mut style,
            Some("--output") => &mut output,
            _ => return Err("unknown argument".into()),
        };
        if slot.replace(value).is_some() {
            return Err("duplicate argument".into());
        }
    }
    jjokgyeol_proof::render(
        &PathBuf::from(runtime.ok_or("missing --runtime")?),
        &PathBuf::from(source.ok_or("missing --source")?),
        &PathBuf::from(style.ok_or("missing --style")?),
        &PathBuf::from(output.ok_or("missing --output")?),
    )
}
fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
