#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn suggested_name_cannot_escape_or_use_special_names() {
        assert_eq!(safe_name("../../원고.pdf"), "원고.pdf");
        assert_eq!(safe_name(".."), "untitled");
        assert_eq!(safe_name("folder\\evil.pdf"), "evil.pdf");
    }

    #[test]
    fn request_size_counts_utf8_bytes() {
        assert!(within_limit("한글".as_bytes(), 6));
        assert!(!within_limit("한글".as_bytes(), 5));
    }
}

pub fn safe_name(name: &str) -> String {
    let name = name.rsplit(['/', '\\']).next().unwrap_or("").trim();
    let name: String = name
        .chars()
        .filter(|c| !c.is_control() && !matches!(c, ':' | '*' | '?' | '"' | '<' | '>' | '|'))
        .collect();
    if name.is_empty() || name == "." || name == ".." {
        "untitled".into()
    } else {
        name
    }
}

pub fn within_limit(bytes: &[u8], limit: u64) -> bool {
    bytes.len() as u64 <= limit
}
