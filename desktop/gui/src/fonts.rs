use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    fs::OpenOptions,
    io::Read,
    path::{Path, PathBuf},
    sync::LazyLock,
};

const UNAVAILABLE: &str = "글꼴 파일을 읽을 수 없습니다. 기기 글꼴 목록을 다시 여세요.";
const INVALID: &str = "올바른 TrueType 또는 OpenType 글꼴 파일을 선택하세요.";
const COLLECTION: &str = "글꼴 컬렉션은 지원하지 않습니다. 단일 TTF 또는 OTF 파일을 선택하세요.";
static MANIFEST_BYTES: LazyLock<u64> = LazyLock::new(|| crate::limits().request);

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

fn runtime_path(root: &Path, relative: &Path) -> Result<PathBuf, String> {
    use std::path::Component;
    if !matches!(
        relative.components().next(),
        Some(Component::Normal(_) | Component::CurDir | Component::ParentDir)
    ) {
        return Err(UNAVAILABLE.into());
    }
    let path = root
        .join(relative)
        .canonicalize()
        .map_err(|_| UNAVAILABLE)?;
    if !path.starts_with(root) {
        return Err(UNAVAILABLE.into());
    }
    Ok(path)
}

pub fn builtin(runtime: &Path, hash: &str, limit: u64) -> Result<Vec<u8>, String> {
    let lock: Value = serde_json::from_str(include_str!("../../../runtime.lock")).unwrap();
    read_builtin(runtime, &lock, hash, limit)
}

fn locked_font_name<'a>(lock: &'a Value, hash: &str) -> Option<&'a str> {
    let face = lock["fonts"]
        .as_object()?
        .values()
        .flat_map(|family| family.as_object().unwrap().values())
        .find(|face| face["sha256"] == hash);
    if let Some(face) = face {
        return Path::new(face["path"].as_str()?).file_name()?.to_str();
    }
    let (name, _) = lock["document_font_files"]
        .as_object()?
        .iter()
        .find(|(_, record)| record["sha256"] == hash)?;
    (Path::new(name).file_name()?.to_str()? == name).then_some(name)
}

fn read_builtin(runtime: &Path, lock: &Value, hash: &str, limit: u64) -> Result<Vec<u8>, String> {
    let root = runtime.canonicalize().map_err(|_| UNAVAILABLE)?;
    let path = if lock["font_license"]["sha256"] == hash {
        runtime_path(&root, Path::new("licenses/debian/fonts-nanum.copyright"))?
    } else {
        let name = locked_font_name(lock, hash).ok_or(UNAVAILABLE)?;
        let manifest_path = runtime_path(&root, Path::new("runtime.json"))?;
        let metadata = read_file(&manifest_path, *MANIFEST_BYTES).map_err(|_| UNAVAILABLE)?;
        let manifest: Value = serde_json::from_slice(&metadata).map_err(|_| UNAVAILABLE)?;
        let font_dir = runtime_path(
            &root,
            Path::new(manifest["font_dir"].as_str().ok_or(UNAVAILABLE)?),
        )?;
        runtime_path(&font_dir, Path::new(name))?
    };
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    const FONT: &[u8] = include_bytes!("../../../tests/fixtures/fonts/abel/Abel-Regular.ttf");
    const HASH: &str = "8809dcad25318225052f88333e208c5aad4adcb7b2c934c135735ec19aa410b4";
    const FILENAME: &str = "Abel-Regular.ttf";

    fn extra_lock() -> Value {
        json!({"fonts": {}, "document_font_files": {FILENAME: {
            "sha256": HASH, "name": "Abel", "family": "Abel", "weight": "regular", "version": "1"
        }}})
    }

    fn write_manifest(runtime: &Path, font_dir: &Path) {
        fs::write(
            runtime.join("runtime.json"),
            json!({"engine": "bin/lualatex", "font_dir": font_dir}).to_string(),
        )
        .unwrap();
    }

    #[test]
    fn builtin_rejects_invalid_font_dir_even_with_a_default_fonts_directory() {
        let temp = tempfile::tempdir().unwrap();
        let runtime = temp.path().join("runtime");
        let default_dir = runtime.join("fonts");
        fs::create_dir_all(&default_dir).unwrap();
        fs::write(default_dir.join(FILENAME), FONT).unwrap();
        fs::write(runtime.join(FILENAME), FONT).unwrap();
        fs::write(temp.path().join(FILENAME), FONT).unwrap();
        let lock = extra_lock();
        let read = || read_builtin(&runtime, &lock, HASH, u64::MAX);
        assert!(read().is_err());
        for dir in [
            Path::new("missing"),
            Path::new(".."),
            &default_dir,
            Path::new(""),
        ] {
            write_manifest(&runtime, dir);
            assert!(read().is_err(), "{dir:?}");
        }
    }

    #[test]
    fn builtin_reads_only_the_locked_asset_from_the_configured_directory() {
        let temp = tempfile::tempdir().unwrap();
        let runtime = temp.path();
        let dir = Path::new("resources/typefaces");
        fs::create_dir_all(runtime.join(dir)).unwrap();
        write_manifest(runtime, dir);
        let path = runtime.join(dir).join(FILENAME);
        fs::write(&path, FONT).unwrap();
        let legacy = json!({"fonts": {"abel": {"regular": {"path": "/pkg/Abel-Regular.ttf", "sha256": HASH}}}});
        assert_eq!(
            read_builtin(runtime, &legacy, HASH, FONT.len() as u64).unwrap(),
            FONT
        );

        let extra = extra_lock();
        let read = |hash, limit| read_builtin(runtime, &extra, hash, limit);
        assert_eq!(read(HASH, FONT.len() as u64).unwrap(), FONT);
        assert!(read(FILENAME, u64::MAX).is_err());
        assert!(read("Abel", u64::MAX).is_err());
        assert!(read(HASH, FONT.len() as u64 - 1).is_err());
        fs::write(&path, b"modified").unwrap();
        assert!(read(HASH, u64::MAX).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn builtin_confines_the_resolved_font_directory_and_file() {
        use std::os::unix::fs::symlink;
        let temp = tempfile::tempdir().unwrap();
        let runtime = temp.path().join("runtime");
        let dir = runtime.join("faces");
        let lock = extra_lock();
        fs::create_dir(&runtime).unwrap();
        write_manifest(&runtime, Path::new("faces"));
        fs::write(temp.path().join(FILENAME), FONT).unwrap();
        symlink(temp.path(), &dir).unwrap();
        assert!(read_builtin(&runtime, &lock, HASH, u64::MAX).is_err());
        fs::remove_file(&dir).unwrap();
        fs::create_dir(&dir).unwrap();
        fs::write(runtime.join(FILENAME), FONT).unwrap();
        symlink(runtime.join(FILENAME), dir.join(FILENAME)).unwrap();
        assert!(read_builtin(&runtime, &lock, HASH, u64::MAX).is_err());
    }

    #[test]
    fn builtin_bounds_runtime_json_independently_of_font_size() {
        let temp = tempfile::tempdir().unwrap();
        let runtime = temp.path();
        write_manifest(runtime, Path::new("."));
        fs::write(runtime.join(FILENAME), FONT).unwrap();
        let lock = extra_lock();
        let path = runtime.join("runtime.json");
        let mut metadata = fs::read(&path).unwrap();
        metadata.resize(crate::limits().request as usize, b' ');
        fs::write(&path, &metadata).unwrap();
        assert_eq!(
            read_builtin(runtime, &lock, HASH, FONT.len() as u64).unwrap(),
            FONT
        );

        metadata.push(b' ');
        fs::write(&path, metadata).unwrap();
        assert!(read_builtin(runtime, &lock, HASH, u64::MAX).is_err());
    }
}
