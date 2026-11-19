//! Opening a job's files with the system's opener (security review M3).
//!
//! A job's code runs in a sandbox, but the files it makes are opened here, with Launch Services
//! (xdg-open on Linux) and your full rights. Some kinds of file run code when opened: a
//! `.command` runs in Terminal, an `.app` starts, a `.webloc` opens whatever it points at. So:
//!
//! - only files in Ombro's own folders (the home, its jobs and chats, ~/Downloads/Ombro for
//!   copies from a server) and in the shared folders can be opened at all;
//! - a file that runs code is shown in Finder (or Files) instead, with a line saying why, and you
//!   open it yourself if you trust it.
//!
//! The web view has no permission to open paths itself (capabilities/default.json): it asks here.

use std::path::{Path, PathBuf};

use tauri::Manager;
use tauri_plugin_opener::OpenerExt;

/// Kinds that run code, start a program, install something or open whatever they point at.
const RUNS_CODE: &[&str] = &[
    // programs and bundles
    "app", "appimage", "jar", "run", "prefpane", "saver", "action", "workflow",
    // scripts and terminal settings
    "command", "terminal", "tool", "sh", "bash", "zsh", "csh", "tcsh", "ksh", "fish",
    "scpt", "scptd", "applescript",
    // installers and disk images
    "pkg", "mpkg", "dmg", "mobileconfig",
    // shortcuts to somewhere else
    "webloc", "fileloc", "inetloc", "desktop",
];

#[cfg(target_os = "macos")]
const FILE_MANAGER: &str = "Finder";
#[cfg(not(target_os = "macos"))]
const FILE_MANAGER: &str = "Files";

/// Why a file runs code when opened, or None if it doesn't (as far as its name and mode say).
fn runs_code(path: &Path) -> Option<String> {
    let ext = path.extension().and_then(|e| e.to_str()).map(|e| e.to_ascii_lowercase());
    if let Some(ext) = ext {
        if RUNS_CODE.contains(&ext.as_str()) {
            return Some(format!("a .{ext} file runs code when it's opened"));
        }
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if let Ok(meta) = std::fs::metadata(path) {
            if meta.is_file() && meta.permissions().mode() & 0o111 != 0 {
                return Some("it's marked as a program (its execute bit is set)".into());
            }
        }
    }
    None
}

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME").map(PathBuf::from)
}

fn expand(path: &str, home: Option<&Path>) -> PathBuf {
    match (path.strip_prefix("~/"), home) {
        (Some(rest), Some(h)) => h.join(rest),
        _ if path == "~" => home.map(Path::to_path_buf).unwrap_or_else(|| PathBuf::from(path)),
        _ => PathBuf::from(path),
    }
}

/// A YAML scalar as `yaml.safe_dump` writes one: plain, 'single' or "double" quoted.
fn unquote(v: &str) -> String {
    let v = v.trim();
    if v.len() >= 2 && v.starts_with('\'') && v.ends_with('\'') {
        return v[1..v.len() - 1].replace("''", "'");
    }
    if v.len() >= 2 && v.starts_with('"') && v.ends_with('"') {
        return v[1..v.len() - 1].replace("\\\"", "\"").replace("\\\\", "\\");
    }
    v.to_string()
}

/// The shared folders' paths in folders.md (its front matter, as Ombro writes it:
/// `folders: {name: {path: …, access: …}}`, or `name: <path>`).
fn shared_folders(text: &str) -> Vec<String> {
    let mut lines = text.lines();
    if lines.next().map(str::trim_end) != Some("---") {
        return vec![];
    }
    let mut out = Vec::new();
    let mut in_folders = false;
    for line in lines {
        if line.trim_end() == "---" {
            break;
        }
        if line.trim().is_empty() || line.trim_start().starts_with('#') {
            continue;
        }
        let indent = line.len() - line.trim_start().len();
        let body = line.trim();
        if indent == 0 {
            in_folders = body == "folders:";
            continue;
        }
        if !in_folders {
            continue;
        }
        let Some((key, value)) = body.split_once(':') else { continue };
        let value = value.trim();
        if key.trim() == "path" && indent > 2 && !value.is_empty() {
            out.push(unquote(value));
        } else if indent == 2 && !value.is_empty() {
            out.push(unquote(value)); // name: <path>
        }
    }
    out
}

/// The folders whose files may be opened: Ombro's home and visible folder, the copies from a
/// server, and the shared folders. Worked out as Ombro does (home.py): $OMBRO_HOME or
/// ~/.config/ombro; $OMBRO_FILES, or ~/Ombro for the usual home, or the home itself.
fn roots(downloads: Option<PathBuf>) -> Vec<PathBuf> {
    let home = home_dir();
    let default = home.as_ref().map(|h| h.join(".config").join("ombro"));
    let root = match std::env::var("OMBRO_HOME") {
        Ok(h) if !h.is_empty() => Some(expand(&h, home.as_deref())),
        _ => default.clone(),
    };
    let files = match std::env::var("OMBRO_FILES") {
        Ok(f) if !f.is_empty() => Some(expand(&f, home.as_deref())),
        _ if root.is_some() && canon(root.as_deref()) == canon(default.as_deref()) => home.as_ref().map(|h| h.join("Ombro")),
        _ => None,
    };
    let mut out: Vec<PathBuf> = vec![];
    out.extend(root.clone());
    out.extend(files);
    out.extend(downloads.map(|d| d.join("Ombro")));
    if let Some(root) = root {
        if let Ok(text) = std::fs::read_to_string(root.join("folders.md")) {
            out.extend(shared_folders(&text).iter().map(|p| expand(p, home.as_deref())));
        }
    }
    out.iter().filter_map(|p| p.canonicalize().ok()).collect()
}

fn canon(p: Option<&Path>) -> Option<PathBuf> {
    p.map(|p| p.canonicalize().unwrap_or_else(|_| p.to_path_buf()))
}

#[derive(Debug, PartialEq)]
enum Decision {
    Open(PathBuf),
    /// Shown in the file manager instead, and why.
    Show(PathBuf, String),
}

fn decide(path: &str, roots: &[PathBuf]) -> Result<Decision, String> {
    let given = Path::new(path);
    if !given.is_absolute() {
        return Err("That file's place isn't known.".into());
    }
    let real = given
        .canonicalize()
        .map_err(|_| format!("{} isn't there any more.", given.display()))?;
    if !roots.iter().any(|r| real.starts_with(r)) {
        return Err(format!(
            "Ombro opens only files in its own folders and your shared folders, and {} is in neither.",
            given.display()
        ));
    }
    // The name it was given by and the file it leads to (a link could hide one behind the other).
    if let Some(why) = runs_code(given).or_else(|| runs_code(&real)) {
        let name = given.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        return Ok(Decision::Show(
            real,
            format!("Shown in {FILE_MANAGER}, not opened: {name} could run code ({why}). Open it yourself only if you trust it."),
        ));
    }
    Ok(Decision::Open(real))
}

/// Open a file or folder with the app the system uses for its kind, or, for a kind that runs
/// code, show it in the file manager. Returns the line saying why it was only shown, or None.
#[tauri::command]
pub fn open_file(app: tauri::AppHandle, path: String) -> Result<Option<String>, String> {
    let roots = roots(app.path().download_dir().ok());
    match decide(&path, &roots)? {
        Decision::Open(real) => {
            app.opener()
                .open_path(real.to_string_lossy(), None::<&str>)
                .map_err(|e| format!("No app would open {}: {e}", real.display()))?;
            Ok(None)
        }
        Decision::Show(real, why) => {
            app.opener().reveal_item_in_dir(&real).map_err(|e| e.to_string())?;
            Ok(Some(why))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("ombro-opening-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d.canonicalize().unwrap()
    }

    #[test]
    fn kinds_that_run_code() {
        for name in ["Summary.command", "x.APP", "a.sh", "s.scpt", "w.workflow", "i.pkg", "d.dmg", "l.webloc",
            "e.desktop", "Tool.AppImage", "t.terminal", "f.fileloc"] {
            assert!(runs_code(Path::new(name)).is_some(), "{name}");
        }
        for name in ["notes.md", "report.html", "data.csv", "photo.png", "README"] {
            assert!(runs_code(Path::new(name)).is_none(), "{name}");
        }
    }

    #[cfg(unix)]
    #[test]
    fn execute_bit_counts() {
        use std::os::unix::fs::PermissionsExt;
        let d = scratch("exec");
        let f = d.join("notes.txt");
        std::fs::write(&f, "#!/bin/sh\necho hi\n").unwrap();
        assert!(runs_code(&f).is_none());
        std::fs::set_permissions(&f, std::fs::Permissions::from_mode(0o755)).unwrap();
        assert!(runs_code(&f).unwrap().contains("execute bit"));
        // A folder's execute bit is only "you may look inside".
        assert!(runs_code(&d).is_none());
    }

    #[test]
    fn opens_inside_the_roots_and_shows_what_runs() {
        let d = scratch("decide");
        let job = d.join("jobs/j1/workspace");
        std::fs::create_dir_all(&job).unwrap();
        std::fs::write(job.join("notes.md"), "hi").unwrap();
        std::fs::write(job.join("Summary.command"), "rm -rf ~").unwrap();
        std::fs::create_dir_all(job.join("Thing.app")).unwrap();
        let roots = vec![d.clone()];
        let p = |rel: &str| job.join(rel).to_string_lossy().into_owned();
        assert_eq!(decide(&p("notes.md"), &roots).unwrap(), Decision::Open(job.join("notes.md")));
        assert!(matches!(decide(&p("Summary.command"), &roots).unwrap(), Decision::Show(_, why) if why.contains("Summary.command")));
        assert!(matches!(decide(&p("Thing.app"), &roots).unwrap(), Decision::Show(..)));
        // The job's own folder opens in the file manager.
        assert_eq!(decide(&job.to_string_lossy(), &roots).unwrap(), Decision::Open(job.clone()));
    }

    #[test]
    fn refuses_outside_the_roots() {
        let d = scratch("outside");
        let other = scratch("elsewhere");
        std::fs::write(other.join("secret.md"), "x").unwrap();
        let roots = vec![d.clone()];
        assert!(decide(&other.join("secret.md").to_string_lossy(), &roots).is_err());
        assert!(decide(&d.join("../ombro-opening-elsewhere-x/secret.md").to_string_lossy(), &roots).is_err());
        assert!(decide("relative/notes.md", &roots).is_err());
        assert!(decide(&d.join("missing.md").to_string_lossy(), &roots).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn a_link_cant_hide_what_runs_or_where_it_leads() {
        let d = scratch("links");
        let other = scratch("links-out");
        std::fs::write(d.join("run.command"), "x").unwrap();
        std::os::unix::fs::symlink(d.join("run.command"), d.join("notes.md")).unwrap();
        std::fs::write(other.join("secret.md"), "x").unwrap();
        std::os::unix::fs::symlink(other.join("secret.md"), d.join("inside.md")).unwrap();
        let roots = vec![d.clone()];
        assert!(matches!(decide(&d.join("notes.md").to_string_lossy(), &roots).unwrap(), Decision::Show(..)));
        assert!(decide(&d.join("inside.md").to_string_lossy(), &roots).is_err());
    }

    #[test]
    fn reads_the_shared_folders() {
        let text = "---\nfolders:\n  demo-files:\n    path: ~/ombro-demo-files\n    access: read\n  \
                    brand: '~/Documents/It''s brand'\n  photos:\n    path: \"/Volumes/Photos\"\n    access: write\n\
                    declined:\n- /tmp/no\n---\n\nbody\nfolders:\n  x:\n    path: /never\n";
        assert_eq!(shared_folders(text), vec!["~/ombro-demo-files", "~/Documents/It's brand", "/Volumes/Photos"]);
        assert!(shared_folders("no front matter").is_empty());
        assert_eq!(expand("~/a", Some(Path::new("/Users/x"))), PathBuf::from("/Users/x/a"));
        assert_eq!(expand("/abs", Some(Path::new("/Users/x"))), PathBuf::from("/abs"));
    }
}
