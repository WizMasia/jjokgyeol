#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn suggested_name_cannot_escape_or_use_special_names() {
        assert_eq!(safe_name("../../원고.pdf"), "원고.pdf");
        assert_eq!(safe_name(".."), "untitled");
        assert_eq!(safe_name("folder\\evil.pdf"), "evil.pdf");
        assert_eq!(safe_name("원고.pdf. ."), "원고.pdf");
        assert_eq!(safe_name("..."), "untitled");
        assert_eq!(safe_name("CON"), "untitled");
        assert_eq!(safe_name("nul.pdf"), "untitled");
        assert_eq!(safe_name("Com1.txt"), "untitled");
        assert_eq!(safe_name("lpt9.json"), "untitled");
        assert_eq!(safe_name("console.pdf"), "console.pdf");
    }

    #[test]
    fn header_filename_decodes_utf8_and_sanitizes() {
        assert_eq!(decode_header_filename("%EC%9B%90%EA%B3%A0.pdf"), "원고.pdf");
        assert_eq!(decode_header_filename("..%2F..%2Fevil.pdf"), "evil.pdf");
        assert_eq!(decode_header_filename("CON.pdf"), "untitled");
        assert_eq!(decode_header_filename(""), "untitled");
    }

    #[test]
    fn request_size_counts_utf8_bytes() {
        assert!(within_limit("한글".as_bytes(), 6));
        assert!(!within_limit("한글".as_bytes(), 5));
    }
}

fn is_windows_reserved_device(name: &str) -> bool {
    let stem = name.split('.').next().unwrap_or("").trim();
    let upper = stem.to_ascii_uppercase();
    matches!(
        upper.as_str(),
        "CON"
            | "PRN"
            | "AUX"
            | "NUL"
            | "COM1"
            | "COM2"
            | "COM3"
            | "COM4"
            | "COM5"
            | "COM6"
            | "COM7"
            | "COM8"
            | "COM9"
            | "LPT1"
            | "LPT2"
            | "LPT3"
            | "LPT4"
            | "LPT5"
            | "LPT6"
            | "LPT7"
            | "LPT8"
            | "LPT9"
    )
}

/// 파일 저장 대화상자에 제안할 파일명을 안전하게 정규화합니다.
/// Sanitize a suggested filename against traversal, trailing dots/spaces, and Windows device names.
pub fn safe_name(name: &str) -> String {
    let name = name.rsplit(['/', '\\']).next().unwrap_or("").trim();
    let cleaned: String = name
        .chars()
        .filter(|c| !c.is_control() && !matches!(c, ':' | '*' | '?' | '"' | '<' | '>' | '|'))
        .collect();
    let trimmed = cleaned.trim().trim_end_matches(['.', ' ']);
    if trimmed.is_empty()
        || trimmed == "."
        || trimmed == ".."
        || is_windows_reserved_device(trimmed)
    {
        "untitled".into()
    } else {
        trimmed.to_owned()
    }
}

fn hex_val(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

/// IPC 헤더의 퍼센트 인코딩된 UTF-8 파일명을 디코딩하고 안전하게 정규화합니다.
/// Percent-decode a UTF-8 IPC header filename and pass it through `safe_name`.
pub fn decode_header_filename(raw: &str) -> String {
    let bytes = raw.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let (Some(hi), Some(lo)) = (hex_val(bytes[i + 1]), hex_val(bytes[i + 2])) {
                out.push((hi << 4) | lo);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    match String::from_utf8(out) {
        Ok(decoded) => safe_name(&decoded),
        Err(_) => "untitled".into(),
    }
}

pub fn within_limit(bytes: &[u8], limit: u64) -> bool {
    bytes.len() as u64 <= limit
}
