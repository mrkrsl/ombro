//! "Save to this computer": copies of a remote server's files, kept under ~/Downloads/Ombro.
//!
//! The web view fetches a file's bytes from serve and hands them here with the relative path serve
//! suggested (`X-Ombro-Save-As`, such as `<job id>/workspace/notes.md`). Nothing outside
//! ~/Downloads/Ombro can be written or read back: a path with `..`, an absolute path or an empty
//! part is refused. Reading back is for a draft edited by hand, sent back to the server on Done.

use std::path::{Component, Path, PathBuf};

use tauri::ipc::{InvokeBody, Request, Response};
use tauri::Manager;

fn base(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let downloads = app.path().download_dir().map_err(|e| format!("no Downloads folder: {e}"))?;
    Ok(downloads.join("Ombro"))
}

/// The real path for a relative one, if it stays inside the Ombro folder.
fn inside(base: &Path, rel: &str) -> Result<PathBuf, String> {
    let rel = Path::new(rel);
    let mut out = base.to_path_buf();
    let mut parts = 0;
    for part in rel.components() {
        match part {
            Component::Normal(p) => {
                out.push(p);
                parts += 1;
            }
            _ => return Err("that file name isn't allowed".into()),
        }
    }
    if parts == 0 {
        return Err("no file name".into());
    }
    Ok(out)
}

fn percent_decode(text: &str) -> Result<String, String> {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            let hex = std::str::from_utf8(&bytes[i + 1..i + 3]).map_err(|_| "bad name")?;
            out.push(u8::from_str_radix(hex, 16).map_err(|_| "bad name")?);
            i += 3;
        } else {
            out.push(bytes[i]);
            i += 1;
        }
    }
    String::from_utf8(out).map_err(|_| "bad name".into())
}

/// Bytes in the body, the relative path (percent-encoded) in the `x-save-as` header. Returns the
/// full path of the copy.
#[tauri::command]
pub fn save_download(app: tauri::AppHandle, request: Request<'_>) -> Result<String, String> {
    let InvokeBody::Raw(data) = request.body() else {
        return Err("expected the file's bytes".into());
    };
    let rel = request
        .headers()
        .get("x-save-as")
        .and_then(|v| v.to_str().ok())
        .ok_or("no file name")?;
    let path = inside(&base(&app)?, &percent_decode(rel)?)?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("couldn't make {}: {e}", parent.display()))?;
    }
    std::fs::write(&path, data).map_err(|e| format!("couldn't save {}: {e}", path.display()))?;
    Ok(path.to_string_lossy().into_owned())
}

/// The bytes of a copy saved earlier, by its relative path.
#[tauri::command]
pub fn read_saved(app: tauri::AppHandle, rel: String) -> Result<Response, String> {
    let path = inside(&base(&app)?, &rel)?;
    let data = std::fs::read(&path).map_err(|e| format!("couldn't read {}: {e}", path.display()))?;
    Ok(Response::new(data))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stays_inside() {
        let b = Path::new("/Users/x/Downloads/Ombro");
        assert_eq!(inside(b, "j1/workspace/a.md").unwrap(), b.join("j1/workspace/a.md"));
        assert!(inside(b, "../secrets").is_err());
        assert!(inside(b, "j1/../../x").is_err());
        assert!(inside(b, "/etc/passwd").is_err());
        assert!(inside(b, "").is_err());
        assert!(inside(b, "./a").is_err());
    }

    #[test]
    fn decodes_names() {
        assert_eq!(percent_decode("j1/caf%C3%A9%20notes.md").unwrap(), "j1/café notes.md");
        assert_eq!(percent_decode("plain").unwrap(), "plain");
        assert!(percent_decode("bad%ZZ").is_err());
    }
}
