//! The `ombro` command in a terminal: the app's own command line (ombro-serve, the whole of
//! `ombro`) on the PATH, so the terminal and the app are one Ombro at one version.
//!
//! - macOS: "Install ‘ombro’ Command…" in the app menu links /usr/local/bin/ombro to
//!   Ombro.app/Contents/MacOS/ombro-serve, asking for an administrator password when that folder
//!   needs one (as VS Code does for `code`). The link points into the app, so the app's updates
//!   update the command too.
//! - AppImage: the same item, in the tray menu (Linux has no menu bar), writes a two-line script to
//!   ~/.local/bin/ombro that runs `<the .AppImage> --ombro "$@"` (main.rs hands that to the
//!   sidecar). A link won't do: the sidecar is in the AppImage's mount, which goes when it quits.
//! - The .deb and .rpm put /usr/bin/ombro in themselves (linux/postinst.sh) and Homebrew's cask
//!   links its own, so they have no item.
//!
//! Another `ombro` (uv's or pipx's) is never replaced. Both use the same settings and home, so
//! either works; the item says which one runs first.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use tauri::{AppHandle, Runtime};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

/// The menu item's id, in the app menu (macOS) or the tray menu (an AppImage).
pub const ID: &str = "app:ombro-command";

const NAME: &str = "ombro";
/// The first two lines of the script an AppImage writes, so it knows its own.
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
const SCRIPT_HEAD: &str = "#!/bin/sh\n# The ombro command from Ombro's AppImage, written by the app (Install ‘ombro’ Command…).\n";

/// The item's words: Remove once the app's command is in place, Install otherwise.
pub fn label() -> &'static str {
    if installed() {
        "Remove ‘ombro’ Command…"
    } else {
        "Install ‘ombro’ Command…"
    }
}

/// Whether this app can install the command: always on macOS; on Linux only from an AppImage.
pub fn available() -> bool {
    source().is_some()
}

/// Where the command goes.
fn target() -> PathBuf {
    if cfg!(target_os = "macos") {
        PathBuf::from("/usr/local/bin").join(NAME)
    } else {
        home().join(".local/bin").join(NAME)
    }
}

fn home() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/"))
}

/// What the command runs: the sidecar beside this binary (macOS), or the .AppImage file (Linux).
fn source() -> Option<PathBuf> {
    if cfg!(target_os = "macos") {
        let exe = std::env::current_exe().ok()?;
        Some(exe.parent()?.join("ombro-serve")).filter(|p| p.is_file())
    } else {
        appimage()
    }
}

fn appimage() -> Option<PathBuf> {
    std::env::var_os("APPIMAGE").map(PathBuf::from).filter(|p| p.is_file())
}

/// The app's command is at the target and works.
fn installed() -> bool {
    let t = target();
    is_ours(&t) && works(&t)
}

/// Put there by an Ombro app: a link into an Ombro.app (macOS), or the AppImage's script.
fn is_ours(path: &Path) -> bool {
    if cfg!(target_os = "macos") {
        std::fs::read_link(path).is_ok_and(|to| points_into_app(&to))
    } else {
        std::fs::read_to_string(path).is_ok_and(|text| text.starts_with(SCRIPT_HEAD))
    }
}

/// A path to an Ombro app's bundled command: …/Contents/MacOS/ombro-serve, or /usr/bin/ombro-serve
/// (the .deb or .rpm).
fn points_into_app(to: &Path) -> bool {
    to.ends_with("Contents/MacOS/ombro-serve") || to == Path::new("/usr/bin/ombro-serve")
}

/// What it runs is still there (the app may have moved since).
fn works(path: &Path) -> bool {
    if cfg!(target_os = "macos") {
        path.is_file()
    } else {
        std::fs::read_to_string(path).ok().and_then(|t| script_runs(&t)).is_some_and(|p| p.is_file())
    }
}

/// The script for an AppImage at this path.
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
fn script(appimage: &Path) -> String {
    format!("{SCRIPT_HEAD}exec {} --ombro \"$@\"\n", sh_quote(&appimage.to_string_lossy()))
}

/// The AppImage a script of ours runs.
fn script_runs(text: &str) -> Option<PathBuf> {
    let line = text.strip_prefix(SCRIPT_HEAD)?.lines().next()?;
    let quoted = line.strip_prefix("exec ")?.strip_suffix(" --ombro \"$@\"")?;
    Some(PathBuf::from(sh_unquote(quoted)?))
}

fn sh_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

fn sh_unquote(s: &str) -> Option<String> {
    let inner = s.strip_prefix('\'')?.strip_suffix('\'')?;
    Some(inner.replace("'\\''", "'"))
}

/// An AppImage that has moved since it wrote the script: point the script at where it is now.
/// At start; the AppImage's own updates keep its path, so this is only for a file moved by hand.
pub fn refresh_at_start() {
    #[cfg(target_os = "linux")]
    if let Some(image) = appimage() {
        let t = target();
        if let Ok(text) = std::fs::read_to_string(&t) {
            if text.starts_with(SCRIPT_HEAD) && script_runs(&text).as_deref() != Some(image.as_path()) {
                let _ = std::fs::write(&t, script(&image));
            }
        }
    }
}

// -- which `ombro` runs first ------------------------------------------------------------------

/// The folders a terminal looks in, in order: the login shell's PATH (an app started from the
/// Dock or the app grid has a short one of its own), or this process's if the shell won't say.
fn terminal_path() -> Vec<PathBuf> {
    let from_shell = shell_path();
    let path = from_shell.or_else(|| std::env::var("PATH").ok()).unwrap_or_default();
    let mut dirs: Vec<PathBuf> = Vec::new();
    for d in std::env::split_paths(&path) {
        if d.is_absolute() && !dirs.contains(&d) {
            dirs.push(d);
        }
    }
    dirs
}

/// The PATH an interactive login shell sets up (where uv and Homebrew add theirs), or None if it
/// takes more than a few seconds or says nothing.
fn shell_path() -> Option<String> {
    const MARK: &str = "__OMBRO_PATH__=";
    let shell = std::env::var("SHELL").ok().filter(|s| s.starts_with('/')).unwrap_or_else(|| "/bin/sh".into());
    let mut child = Command::new(shell)
        .args(["-ilc", &format!("printf '\\n{MARK}%s\\n' \"$PATH\"")])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if started.elapsed() < Duration::from_secs(5) => std::thread::sleep(Duration::from_millis(50)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    }
    let mut out = String::new();
    use std::io::Read;
    child.stdout.take()?.read_to_string(&mut out).ok()?;
    out.lines().rev().find_map(|l| l.strip_prefix(MARK)).map(str::to_string)
}

/// An `ombro` found in one of the folders.
#[derive(Debug, Clone, PartialEq)]
struct Found {
    path: PathBuf,
    /// The app's command (a link into an Ombro app, or an AppImage's script), not uv's or pipx's.
    ours: bool,
}

fn find_all(dirs: &[PathBuf]) -> Vec<Found> {
    use std::os::unix::fs::PermissionsExt;
    dirs.iter()
        .map(|d| d.join(NAME))
        .filter(|p| std::fs::metadata(p).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0))
        .map(|p| {
            let ours = is_ours(&p) || std::fs::canonicalize(&p).is_ok_and(|r| points_into_app(&r));
            Found { path: p, ours }
        })
        .collect()
}

/// What installing would do, given the `ombro` commands a terminal finds (in PATH order).
#[derive(Debug, PartialEq)]
enum Plan {
    /// Something not the app's is at the target already: leave it.
    Occupied,
    /// The app's command is already on the PATH somewhere else (Homebrew's link, the .deb's).
    AlreadyThere(PathBuf),
    /// Install. `first`: another `ombro` that a terminal runs before this one. `later`: one it
    /// runs after. `off_path`: the target's folder isn't on the PATH at all.
    Install { first: Option<PathBuf>, later: Option<PathBuf>, off_path: bool },
}

fn plan(target: &Path, target_ours: bool, target_exists: bool, dirs: &[PathBuf], found: &[Found]) -> Plan {
    if target_exists && !target_ours {
        return Plan::Occupied;
    }
    if let Some(f) = found.iter().find(|f| f.ours && f.path != target) {
        return Plan::AlreadyThere(f.path.clone());
    }
    let folder = target.parent().map(Path::to_path_buf).unwrap_or_default();
    let at = dirs.iter().position(|d| *d == folder);
    let others: Vec<&Found> = found.iter().filter(|f| f.path != target && !f.ours).collect();
    let pos = |f: &Found| f.path.parent().and_then(|p| dirs.iter().position(|d| d == p));
    let first = others.iter().find(|f| match (pos(f), at) {
        (Some(p), Some(a)) => p < a,
        (Some(_), None) => true,
        _ => false,
    });
    let later = others.iter().find(|f| match (pos(f), at) {
        (Some(p), Some(a)) => p > a,
        _ => false,
    });
    Plan::Install { first: first.map(|f| f.path.clone()), later: later.map(|f| f.path.clone()), off_path: at.is_none() }
}

// -- the menu item -------------------------------------------------------------------------------

/// The item was picked: install the command, or remove it, then say how it went. `done` runs
/// afterwards with the item's new words. Off the main thread: it may wait on a password.
/// One at a time: a second pick while the first waits on a dialog or a password is ignored.
pub fn pick<R: Runtime>(app: &AppHandle<R>, done: impl FnOnce(&'static str) + Send + 'static) {
    use std::sync::atomic::{AtomicBool, Ordering};
    static BUSY: AtomicBool = AtomicBool::new(false);
    if BUSY.swap(true, Ordering::SeqCst) {
        return;
    }
    let app = app.clone();
    std::thread::spawn(move || {
        if installed() {
            remove(&app);
        } else {
            install(&app);
        }
        BUSY.store(false, Ordering::SeqCst);
        done(label());
    });
}

fn tell<R: Runtime>(app: &AppHandle<R>, kind: MessageDialogKind, title: &str, text: String) {
    app.dialog().message(text).title(title).kind(kind).blocking_show();
}

fn install<R: Runtime>(app: &AppHandle<R>) {
    let Some(source) = source() else { return };
    let t = target();
    let dirs = terminal_path();
    let found = find_all(&dirs);
    let exists = std::fs::symlink_metadata(&t).is_ok();
    let title = "Install ‘ombro’ Command";
    match plan(&t, is_ours(&t), exists, &dirs, &found) {
        Plan::Occupied => tell(
            app,
            MessageDialogKind::Info,
            title,
            format!(
                "There's already an ombro command at {}, not the app's (uv or pipx may have put it there). \
                 Ombro leaves it in place: it uses the same settings and home as the app, so it works as it is.\n\n\
                 ombro --version tells you which version you have.",
                t.display()
            ),
        ),
        Plan::AlreadyThere(p) => tell(
            app,
            MessageDialogKind::Info,
            title,
            format!("The app's ombro command is already installed, at {}. Type ombro in a terminal to use it.", p.display()),
        ),
        Plan::Install { first, later, off_path } => match put(&source, &t) {
            Ok(false) => {} // the password was refused or cancelled
            Err(e) => tell(app, MessageDialogKind::Error, title, format!("Ombro couldn't install the ombro command: {e}")),
            Ok(true) => {
                let mut text = format!("Installed at {}. Open a new terminal window and type ombro.", t.display());
                if let Some(p) = first {
                    text = format!(
                        "Installed at {}. There's another ombro command, at {}, which comes first on your PATH, so a \
                         terminal runs that one. Ombro has left it in place: it uses the same settings and home as \
                         the app.\n\nombro --version tells you which version you have.",
                        t.display(),
                        p.display()
                    );
                } else if let Some(p) = later {
                    text.push_str(&format!(
                        "\n\nThere's another ombro command, at {}. This one comes first on your PATH; the other is left in place.",
                        p.display()
                    ));
                }
                if off_path {
                    let folder = t.parent().unwrap_or(&t).display().to_string();
                    text.push_str(&format!("\n\n{folder} isn't on your PATH yet: add it in your shell's settings, or run {}.", t.display()));
                }
                tell(app, MessageDialogKind::Info, title, text);
            }
        },
    }
}

fn remove<R: Runtime>(app: &AppHandle<R>) {
    let t = target();
    let title = "Remove ‘ombro’ Command";
    let sure = app
        .dialog()
        .message(format!("This removes {}. The app itself isn't changed, and you can install the command again from this menu.", t.display()))
        .title(title)
        .kind(MessageDialogKind::Info)
        .buttons(MessageDialogButtons::OkCancelCustom("Remove".into(), "Cancel".into()))
        .blocking_show();
    if !sure {
        return;
    }
    if let Err(e) = take_away(&t) {
        tell(app, MessageDialogKind::Error, title, format!("Ombro couldn't remove {}: {e}", t.display()));
    }
}

/// Put the command at `t`. Ok(false): the person cancelled the password prompt.
fn put(source: &Path, t: &Path) -> Result<bool, String> {
    if cfg!(target_os = "macos") {
        let direct = (|| {
            std::fs::create_dir_all(t.parent().unwrap())?;
            if std::fs::symlink_metadata(t).is_ok() {
                std::fs::remove_file(t)?;
            }
            std::os::unix::fs::symlink(source, t)
        })();
        match direct {
            Ok(()) => Ok(true),
            Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied => as_admin(
                &format!("/bin/mkdir -p {} && /bin/ln -sfn {} {}", sh_quote(&t.parent().unwrap().to_string_lossy()), sh_quote(&source.to_string_lossy()), sh_quote(&t.to_string_lossy())),
                "Ombro wants to install the ombro command in /usr/local/bin.",
            ),
            Err(e) => Err(e.to_string()),
        }
    } else {
        use std::os::unix::fs::PermissionsExt;
        let write = || -> std::io::Result<()> {
            std::fs::create_dir_all(t.parent().unwrap())?;
            std::fs::write(t, script(source))?;
            std::fs::set_permissions(t, std::fs::Permissions::from_mode(0o755))
        };
        write().map(|()| true).map_err(|e| e.to_string())
    }
}

fn take_away(t: &Path) -> Result<(), String> {
    match std::fs::remove_file(t) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) if cfg!(target_os = "macos") && e.kind() == std::io::ErrorKind::PermissionDenied => {
            as_admin(&format!("/bin/rm -f {}", sh_quote(&t.to_string_lossy())), "Ombro wants to remove the ombro command from /usr/local/bin.").map(|_| ())
        }
        Err(e) => Err(e.to_string()),
    }
}

/// Run a shell command as root, after macOS's own password prompt. Ok(false): cancelled.
fn as_admin(script: &str, prompt: &str) -> Result<bool, String> {
    let apple = format!(
        "do shell script \"{}\" with prompt \"{}\" with administrator privileges",
        applescript_escape(script),
        applescript_escape(prompt)
    );
    let out = Command::new("/usr/bin/osascript").args(["-e", &apple]).output().map_err(|e| e.to_string())?;
    if out.status.success() {
        return Ok(true);
    }
    let err = String::from_utf8_lossy(&out.stderr);
    if err.contains("-128") {
        return Ok(false); // User canceled.
    }
    Err(err.trim().to_string())
}

fn applescript_escape(s: &str) -> String {
    s.replace('\\', "\\\\").replace('"', "\\\"")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dirs(list: &[&str]) -> Vec<PathBuf> {
        list.iter().map(PathBuf::from).collect()
    }

    fn found(path: &str, ours: bool) -> Found {
        Found { path: PathBuf::from(path), ours }
    }

    #[test]
    fn something_else_at_the_target_is_left_alone() {
        let t = Path::new("/usr/local/bin/ombro");
        let p = plan(t, false, true, &dirs(&["/usr/local/bin"]), &[found("/usr/local/bin/ombro", false)]);
        assert_eq!(p, Plan::Occupied);
    }

    #[test]
    fn homebrews_link_means_its_already_there() {
        let t = Path::new("/usr/local/bin/ombro");
        let d = dirs(&["/opt/homebrew/bin", "/usr/local/bin"]);
        let p = plan(t, false, false, &d, &[found("/opt/homebrew/bin/ombro", true)]);
        assert_eq!(p, Plan::AlreadyThere(PathBuf::from("/opt/homebrew/bin/ombro")));
    }

    #[test]
    fn uv_first_on_the_path_is_named() {
        let t = Path::new("/usr/local/bin/ombro");
        let d = dirs(&["/Users/me/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]);
        let p = plan(t, false, false, &d, &[found("/Users/me/.local/bin/ombro", false)]);
        assert_eq!(p, Plan::Install { first: Some(PathBuf::from("/Users/me/.local/bin/ombro")), later: None, off_path: false });
    }

    #[test]
    fn one_later_on_the_path_runs_second() {
        let t = Path::new("/usr/local/bin/ombro");
        let d = dirs(&["/usr/local/bin", "/Users/me/.local/bin"]);
        let p = plan(t, false, false, &d, &[found("/Users/me/.local/bin/ombro", false)]);
        assert_eq!(p, Plan::Install { first: None, later: Some(PathBuf::from("/Users/me/.local/bin/ombro")), off_path: false });
    }

    #[test]
    fn a_stale_link_of_ours_is_replaced() {
        let t = Path::new("/usr/local/bin/ombro");
        let p = plan(t, true, true, &dirs(&["/usr/local/bin"]), &[]);
        assert_eq!(p, Plan::Install { first: None, later: None, off_path: false });
    }

    #[test]
    fn a_folder_off_the_path_is_said() {
        let t = Path::new("/home/me/.local/bin/ombro");
        let p = plan(t, false, false, &dirs(&["/usr/bin"]), &[]);
        assert_eq!(p, Plan::Install { first: None, later: None, off_path: true });
    }

    #[test]
    fn the_script_names_its_appimage_and_reads_back() {
        let image = Path::new("/home/me/Apps/Ombro's app.AppImage");
        let text = script(image);
        assert!(text.starts_with("#!/bin/sh\n"));
        assert!(text.ends_with("exec '/home/me/Apps/Ombro'\\''s app.AppImage' --ombro \"$@\"\n"));
        assert_eq!(script_runs(&text).as_deref(), Some(image));
        assert_eq!(script_runs("#!/bin/sh\nexec ombro\n"), None);
    }

    #[test]
    fn app_links_are_recognised() {
        assert!(points_into_app(Path::new("/Applications/Ombro.app/Contents/MacOS/ombro-serve")));
        assert!(points_into_app(Path::new("/usr/bin/ombro-serve")));
        assert!(!points_into_app(Path::new("/Users/me/.local/share/uv/tools/ombro/bin/ombro")));
    }

    #[test]
    fn applescript_quotes_are_escaped() {
        assert_eq!(applescript_escape(r#"ln -s '/a "b"\c'"#), r#"ln -s '/a \"b\"\\c'"#);
    }

    #[test]
    fn ombro_files_on_the_path_are_found_in_order() {
        use std::os::unix::fs::PermissionsExt;
        let root = std::env::temp_dir().join(format!("ombro-command-test-{}", std::process::id()));
        let (a, b, c) = (root.join("a"), root.join("b"), root.join("c"));
        for d in [&a, &b, &c] {
            std::fs::create_dir_all(d).unwrap();
        }
        std::fs::write(b.join("ombro"), "#!/bin/sh\n").unwrap();
        std::fs::set_permissions(b.join("ombro"), std::fs::Permissions::from_mode(0o755)).unwrap();
        std::fs::write(c.join("ombro"), "not runnable").unwrap(); // no x bit: not a command
        let got = find_all(&[a.clone(), b.clone(), c.clone()]);
        std::fs::remove_dir_all(&root).unwrap();
        assert_eq!(got, vec![Found { path: b.join("ombro"), ours: false }]);
    }
}
