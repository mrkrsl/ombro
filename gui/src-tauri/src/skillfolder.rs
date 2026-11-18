//! "Add from a folder" in Settings › Skills: read a skill folder chosen with the system's picker,
//! so its files can be sent to serve, which may run on another computer.
//!
//! Only what an import copies is read: the folder's SKILL.md and the text files under
//! references/, each at most 1 MB. Everything else (scripts, pictures, links, hidden files) is
//! named in `skipped` and never read. Serve checks it all again, as `ombro skills add` does.

use std::collections::BTreeMap;
use std::path::Path;

use serde::Serialize;

const MAX_BYTES: u64 = 1_000_000;
const MAX_FILES: usize = 200;
const NAMES: [&str; 2] = ["SKILL.md", "skill.md"];

#[derive(Serialize, Debug)]
pub struct SkillFolder {
    /// The folder's own name: the skill's name when its SKILL.md gives none.
    folder: String,
    path: String,
    /// Path in the folder (with /) → text.
    files: BTreeMap<String, String>,
    skipped: Vec<String>,
}

fn walk(root: &Path, dir: &Path, out: &mut SkillFolder) -> Result<(), String> {
    let mut entries: Vec<_> = std::fs::read_dir(dir)
        .map_err(|e| format!("couldn't read {}: {e}", dir.display()))?
        .filter_map(|e| e.ok())
        .collect();
    entries.sort_by_key(|e| e.file_name());
    for entry in entries {
        let path = entry.path();
        let rel = path
            .strip_prefix(root)
            .map_err(|_| "a file outside the folder".to_string())?
            .components()
            .map(|c| c.as_os_str().to_string_lossy().into_owned())
            .collect::<Vec<_>>()
            .join("/");
        if rel.split('/').any(|p| p.starts_with('.')) {
            continue; // hidden: .git, .DS_Store
        }
        let meta = std::fs::symlink_metadata(&path).map_err(|e| format!("couldn't read {rel}: {e}"))?;
        if meta.is_dir() {
            walk(root, &path, out)?;
            continue;
        }
        let wanted = NAMES.contains(&rel.as_str()) || rel.starts_with("references/");
        if !wanted || !meta.is_file() || meta.len() > MAX_BYTES || out.files.len() >= MAX_FILES {
            out.skipped.push(rel);
            continue;
        }
        match std::fs::read(&path).ok().and_then(|b| String::from_utf8(b).ok()) {
            Some(text) => {
                out.files.insert(rel, text);
            }
            None => out.skipped.push(rel),
        }
    }
    Ok(())
}

pub fn read(path: &Path) -> Result<SkillFolder, String> {
    if !path.is_dir() {
        return Err(format!("{} isn't a folder", path.display()));
    }
    if !NAMES.iter().any(|n| path.join(n).is_file()) {
        return Err(format!("There's no SKILL.md in {}. Choose the folder that holds it.", path.display()));
    }
    let mut out = SkillFolder {
        folder: path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default(),
        path: path.to_string_lossy().into_owned(),
        files: BTreeMap::new(),
        skipped: Vec::new(),
    };
    walk(path, path, &mut out)?;
    Ok(out)
}

#[tauri::command]
pub fn read_skill_folder(path: String) -> Result<SkillFolder, String> {
    read(Path::new(&path))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_what_an_import_copies() {
        let tmp = std::env::temp_dir().join(format!("ombro-skillfolder-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);
        let dir = tmp.join("kettle-survey");
        std::fs::create_dir_all(dir.join("references")).unwrap();
        std::fs::create_dir_all(dir.join("scripts")).unwrap();
        std::fs::write(dir.join("SKILL.md"), "---\nname: kettle-survey\ndescription: x\n---\nDo it.\n").unwrap();
        std::fs::write(dir.join("references/notes.md"), "notes").unwrap();
        std::fs::write(dir.join("references/pic.png"), [0xff, 0xfe, 0x00]).unwrap();
        std::fs::write(dir.join("scripts/run.sh"), "echo").unwrap();
        std::fs::write(dir.join(".DS_Store"), "x").unwrap();
        let got = read(&dir).unwrap();
        assert_eq!(got.folder, "kettle-survey");
        assert_eq!(got.files.keys().cloned().collect::<Vec<_>>(), vec!["SKILL.md", "references/notes.md"]);
        assert_eq!(got.skipped, vec!["references/pic.png", "scripts/run.sh"]);
        assert!(read(&tmp).is_err()); // no SKILL.md
        let _ = std::fs::remove_dir_all(&tmp);
    }
}
