use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    fs::OpenOptions,
    io::Read,
    path::{Path, PathBuf},
};

const UNAVAILABLE: &str = "글꼴 파일을 읽을 수 없습니다. 기기 글꼴 목록을 다시 여세요.";
const INVALID: &str = "올바른 TrueType 또는 OpenType 글꼴 파일을 선택하세요.";
const COLLECTION: &str = "글꼴 컬렉션은 지원하지 않습니다. 단일 TTF 또는 OTF 파일을 선택하세요.";

struct Entry {
    path: PathBuf,
    name: String,
    reason: Option<&'static str>,
}

#[derive(Default)]
pub struct Catalog(HashMap<String, Entry>);

impl Catalog {
    pub fn new(mut entries: Vec<(PathBuf, String)>) -> Self {
        let mut catalog = Self::default();
        entries.sort();
        for (path, name) in entries {
            let Ok(path) = path.canonicalize() else {
                continue;
            };
            let ext = path
                .extension()
                .and_then(|s| s.to_str())
                .unwrap_or("")
                .to_ascii_lowercase();
            let reason = match ext.as_str() {
                "ttf" | "otf" => None,
                "ttc" | "otc" => Some(COLLECTION),
                _ => Some(INVALID),
            };
            let id = format!("{:x}", Sha256::digest(path.as_os_str().as_encoded_bytes()));
            catalog.0.entry(id).or_insert(Entry { path, name, reason });
        }
        catalog
    }

    pub fn list(&self) -> Vec<Value> {
        let mut items: Vec<_> = self.0.iter().collect();
        items.sort_by(|(id, a), (other, b)| a.name.cmp(&b.name).then(id.cmp(other)));
        items
            .into_iter()
            .map(|(id, e)| json!({"id": id, "name": e.name, "reason": e.reason}))
            .collect()
    }

    pub fn read(&self, id: &str, limit: u64) -> Result<Vec<u8>, String> {
        let entry = self.0.get(id).ok_or(UNAVAILABLE)?;
        if let Some(reason) = entry.reason {
            return Err(reason.into());
        }
        let data = read_file(&entry.path, limit)?;
        if data.starts_with(b"ttcf") {
            return Err(COLLECTION.into());
        }
        if !data.starts_with(b"\0\x01\0\0") && !data.starts_with(b"OTTO") {
            return Err(INVALID.into());
        }
        let face = ttf_parser::Face::parse(&data, 0).map_err(|_| INVALID)?;
        for table in face.raw_face().table_records {
            if u64::from(table.offset) + u64::from(table.length) > data.len() as u64 {
                return Err(INVALID.into());
            }
        }
        Ok(data)
    }
}

fn read_file(path: &Path, limit: u64) -> Result<Vec<u8>, String> {
    if path.canonicalize().ok().as_deref() != Some(path) {
        return Err(UNAVAILABLE.into());
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    let file = options.open(path).map_err(|_| UNAVAILABLE)?;
    let meta = file.metadata().map_err(|_| UNAVAILABLE)?;
    if !meta.is_file() {
        return Err(UNAVAILABLE.into());
    }
    const TOO_LARGE: &str = "글꼴 파일 크기가 허용 한도를 넘습니다.";
    if meta.len() > limit {
        return Err(TOO_LARGE.into());
    }
    let mut bytes = Vec::new();
    file.take(limit.saturating_add(1))
        .read_to_end(&mut bytes)
        .map_err(|_| UNAVAILABLE)?;
    if bytes.len() as u64 > limit {
        return Err(TOO_LARGE.into());
    }
    Ok(bytes)
}

pub fn builtin(runtime: &Path, hash: &str, limit: u64) -> Result<Vec<u8>, String> {
    let lock: Value = serde_json::from_str(include_str!("../../../runtime.lock")).unwrap();
    let relative = if lock["font_license"]["sha256"] == hash {
        PathBuf::from("licenses/debian/fonts-nanum.copyright")
    } else {
        let face = lock["fonts"]
            .as_object()
            .unwrap()
            .values()
            .flat_map(|family| family.as_object().unwrap().values())
            .find(|face| face["sha256"] == hash)
            .ok_or(UNAVAILABLE)?;
        let name = Path::new(face["path"].as_str().unwrap())
            .file_name()
            .unwrap();
        Path::new("fonts").join(name)
    };
    let root = runtime.canonicalize().map_err(|_| UNAVAILABLE)?;
    let path = root
        .join(relative)
        .canonicalize()
        .map_err(|_| UNAVAILABLE)?;
    if !path.starts_with(&root) {
        return Err(UNAVAILABLE.into());
    }
    let bytes = read_file(&path, limit)?;
    if format!("{:x}", Sha256::digest(&bytes)) != hash {
        return Err("설치된 글꼴 또는 고지의 해시가 일치하지 않습니다.".into());
    }
    Ok(bytes)
}

#[cfg(target_os = "macos")]
pub fn installed() -> Result<Catalog, String> {
    let descriptors = core_text::font_collection::create_for_all_families()
        .get_descriptors()
        .ok_or(UNAVAILABLE)?;
    let entries = descriptors
        .iter()
        .filter_map(|d| Some((d.font_path()?, d.display_name())));
    Ok(Catalog::new(entries.collect()))
}

#[cfg(target_os = "linux")]
pub fn installed() -> Result<Catalog, String> {
    use fontconfig::{Fontconfig, ObjectSet, Pattern, FC_FILE, FC_FULLNAME};
    let fc = Fontconfig::new().ok_or(UNAVAILABLE)?;
    let pattern = Pattern::new(&fc).map_err(|_| UNAVAILABLE)?;
    let mut fields = ObjectSet::new(&fc).map_err(|_| UNAVAILABLE)?;
    fields
        .add(FC_FILE)
        .and_then(|()| fields.add(FC_FULLNAME))
        .map_err(|_| UNAVAILABLE)?;
    let fonts = fontconfig::list_fonts(&pattern, Some(&fields)).map_err(|_| UNAVAILABLE)?;
    let entries = fonts.iter().filter_map(|p| {
        Some((
            p.filename().ok()?.into(),
            p.get_string(FC_FULLNAME).ok()?.into(),
        ))
    });
    Ok(Catalog::new(entries.collect()))
}

#[cfg(windows)]
pub fn installed() -> Result<Catalog, String> {
    use std::{ffi::OsString, os::windows::ffi::OsStringExt, ptr};
    use windows::{core::Interface, Win32::Graphics::DirectWrite::*};

    // DirectWrite owns each reference key until its IDWriteFontFile is released.
    unsafe {
        let factory: IDWriteFactory3 =
            DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED).map_err(|_| UNAVAILABLE)?;
        let fonts = factory.GetSystemFontSet().map_err(|_| UNAVAILABLE)?;
        let entries = (0..fonts.GetFontCount()).filter_map(|index| {
            let face = fonts.GetFontFaceReference(index).ok()?;
            if face.GetLocality() != DWRITE_LOCALITY_LOCAL {
                return None;
            }
            let file = face.GetFontFile().ok()?;
            let loader: IDWriteLocalFontFileLoader = file.GetLoader().ok()?.cast().ok()?;
            let (mut key, mut size) = (ptr::null_mut(), 0);
            file.GetReferenceKey(&mut key, &mut size).ok()?;
            let length = loader.GetFilePathLengthFromKey(key, size).ok()? as usize;
            let mut buf = vec![0; length + 1];
            loader.GetFilePathFromKey(key, size, &mut buf).ok()?;
            let path = PathBuf::from(OsString::from_wide(&buf[..length]));
            let mut values = None;
            let mut exists = Default::default();
            fonts
                .GetPropertyValues3(
                    index,
                    DWRITE_FONT_PROPERTY_ID_FULL_NAME,
                    &mut exists,
                    &mut values,
                )
                .ok()?;
            let values = values?;
            let length = values.GetStringLength(0).ok()? as usize;
            let mut name = vec![0; length + 1];
            values.GetString(0, &mut name).ok()?;
            Some((path, String::from_utf16_lossy(&name[..length])))
        });
        Ok(Catalog::new(entries.collect()))
    }
}
