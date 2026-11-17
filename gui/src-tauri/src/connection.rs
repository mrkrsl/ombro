//! Where Ombro runs: on this computer (the bundled serve, as before) or on a server.
//!
//! The choice is kept in the app's config folder as connection.json. A server's device token is
//! kept in the system keychain (the macOS Keychain, or the Secret Service on Linux), never in a
//! file. Whichever it is, the web view only ever talks to 127.0.0.1:
//!
//! - **SSH**: `ssh -N -L` carries a local port to serve's 127.0.0.1:<port> on the server. Pairing
//!   needs no code: the app runs `ombro pair --device <name>` over SSH, with your own SSH key.
//! - **Direct** (a Tailscale address, or Docker on this computer): a small forwarder here passes a
//!   local port through to the address. Pairing takes the code the server printed.
//!
//! The tunnel or forwarder is restarted if it drops; the web view reconnects by itself.

use std::net::SocketAddr;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Mutex;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::Manager;
use tokio::net::{TcpListener, TcpStream};
use tokio::process::{Child, Command};

pub const KEYCHAIN_SERVICE: &str = "app.ombro.desktop";
pub const DEFAULT_PORT: u16 = 8765;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(tag = "mode", rename_all = "lowercase")]
pub enum Connection {
    Local,
    Ssh { host: String, port: u16 },
    Direct { address: String },
}

impl Connection {
    /// The keychain account for this server's token.
    pub fn account(&self) -> String {
        match self {
            Connection::Local => "local".into(),
            Connection::Ssh { host, port } => format!("ssh:{host}:{port}"),
            Connection::Direct { address } => format!("direct:{address}"),
        }
    }
}

/// What the web view is told about the connection, before anything starts.
#[derive(Clone, Debug, Serialize)]
pub struct State {
    /// "unset" (a fresh install: ask), "local", "ssh" or "direct".
    pub mode: String,
    pub host: Option<String>,
    pub port: Option<u16>,
    pub address: Option<String>,
    /// Whether this computer has an Ombro home of its own already.
    pub local_home: bool,
    pub started: bool,
}

/// The tunnel or forwarder in use, kept so it can be stopped on the way out.
#[derive(Default)]
pub struct Remote {
    pub ssh: Mutex<Option<u32>>, // the ssh process id, to kill on exit
    pub pairing: Mutex<Option<Child>>, // a short-lived tunnel, while pairing over SSH with a code
}

/// For trying the app by hand: $OMBRO_APP_DIR keeps connection.json somewhere else, so a test
/// never changes where the installed app connects, and lets a demo home ($OMBRO_HOME) be left out.
fn test_dir() -> Option<PathBuf> {
    std::env::var_os("OMBRO_APP_DIR").map(PathBuf::from)
}

fn config_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let dir = match test_dir() {
        Some(d) => d,
        None => app.path().app_config_dir().map_err(|e| e.to_string())?,
    };
    Ok(dir.join("connection.json"))
}

pub fn load(app: &tauri::AppHandle) -> Option<Connection> {
    let text = std::fs::read_to_string(config_path(app).ok()?).ok()?;
    serde_json::from_str(&text).ok()
}

pub fn save(app: &tauri::AppHandle, conn: &Connection) -> Result<(), String> {
    let path = config_path(app)?;
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let text = serde_json::to_string_pretty(conn).map_err(|e| e.to_string())?;
    std::fs::write(&path, text).map_err(|e| format!("couldn't save {}: {e}", path.display()))
}

/// Whether the usual Ombro home is here (~/.config/ombro, or $OMBRO_HOME).
pub fn local_home_exists() -> bool {
    let root = match std::env::var_os("OMBRO_HOME") {
        Some(h) => PathBuf::from(h),
        None => match std::env::var_os("HOME") {
            Some(h) => PathBuf::from(h).join(".config").join("ombro"),
            None => return false,
        },
    };
    root.join("limits.md").is_file()
}

/// The connection to use at start: a demo or test home ($OMBRO_HOME) is always local; else the
/// one saved; else local when there's a home here already (people who had Ombro before this);
/// else None, and the web view asks.
pub fn at_start(app: &tauri::AppHandle) -> Option<Connection> {
    if std::env::var_os("OMBRO_HOME").is_some() && test_dir().is_none() {
        return Some(Connection::Local);
    }
    if let Some(c) = load(app) {
        return Some(c);
    }
    if local_home_exists() {
        let _ = save(app, &Connection::Local);
        return Some(Connection::Local);
    }
    None
}

pub fn state(conn: Option<&Connection>, started: bool) -> State {
    let mut s = State { mode: "unset".into(), host: None, port: None, address: None, local_home: local_home_exists(), started };
    match conn {
        None => {}
        Some(Connection::Local) => s.mode = "local".into(),
        Some(Connection::Ssh { host, port }) => {
            s.mode = "ssh".into();
            s.host = Some(host.clone());
            s.port = Some(*port);
        }
        Some(Connection::Direct { address }) => {
            s.mode = "direct".into();
            s.address = Some(address.clone());
        }
    }
    s
}

// -- the keychain -------------------------------------------------------------------

pub fn token(conn: &Connection) -> Result<Option<String>, String> {
    let entry = keyring::Entry::new(KEYCHAIN_SERVICE, &conn.account()).map_err(|e| e.to_string())?;
    match entry.get_password() {
        Ok(t) => Ok(Some(t)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(format!("the keychain couldn't be read: {e}")),
    }
}

pub fn keep_token(conn: &Connection, token: &str) -> Result<(), String> {
    let entry = keyring::Entry::new(KEYCHAIN_SERVICE, &conn.account()).map_err(|e| e.to_string())?;
    entry.set_password(token).map_err(|e| format!("the keychain couldn't keep the server's key: {e}"))
}

// -- names and checks ---------------------------------------------------------------

/// What the server lists this computer as: its own name, plainly.
pub fn device_name() -> String {
    let out = if cfg!(target_os = "macos") {
        std::process::Command::new("/usr/sbin/scutil").args(["--get", "ComputerName"]).output()
    } else {
        std::process::Command::new("hostname").output()
    };
    let name = out.ok().filter(|o| o.status.success()).map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string());
    clean_name(&name.unwrap_or_default())
}

/// Letters, digits, spaces and a few marks only, so it can go in a command line over SSH as it is.
pub fn clean_name(name: &str) -> String {
    let kept: String = name
        .chars()
        .map(|c| if c.is_alphanumeric() || " ._-".contains(c) { c } else { ' ' })
        .collect();
    let kept = kept.split_whitespace().collect::<Vec<_>>().join(" ");
    let kept = kept.trim_start_matches(['-', '.', ' ']);
    let kept: String = kept.chars().take(60).collect();
    if kept.is_empty() { "Ombro app".into() } else { kept }
}

/// An SSH destination as typed: `you@server`, `server`, or a name from ~/.ssh/config. Nothing
/// that ssh could read as an option or that a shell would treat specially.
pub fn check_host(host: &str) -> Result<String, String> {
    let host = host.trim();
    if host.is_empty() {
        return Err("Type the server, such as you@server.example.com".into());
    }
    if host.starts_with('-') || !host.chars().all(|c| c.is_alphanumeric() || "@.-_:[]".contains(c)) {
        return Err(format!("{host} isn't an SSH address Ombro can use"));
    }
    Ok(host.to_string())
}

/// An address as typed: `100.101.102.103:8765`, `my-box.tail1234.ts.net`, `127.0.0.1:8765`.
pub fn check_address(address: &str) -> Result<String, String> {
    let address = address.trim().trim_start_matches("http://").trim_end_matches('/');
    if address.is_empty() || address.starts_with('-') || !address.chars().all(|c| c.is_alphanumeric() || ".-_:[]".contains(c)) {
        return Err("Type the server's address, such as 100.101.102.103:8765".into());
    }
    let has_port = address.rsplit_once(':').map(|(_, p)| p.parse::<u16>().is_ok()).unwrap_or(false)
        && !address.ends_with(']');
    Ok(if has_port { address.to_string() } else { format!("{address}:{DEFAULT_PORT}") })
}

fn ssh_program() -> &'static str {
    if std::path::Path::new("/usr/bin/ssh").exists() { "/usr/bin/ssh" } else { "ssh" }
}

fn ssh_options() -> Vec<&'static str> {
    // No password prompts (there's no terminal to type one in): your SSH key or agent signs in.
    // A server seen for the first time is trusted and remembered, as ssh would ask you to.
    vec!["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15"]
}

/// What went wrong with ssh, in plain words.
fn ssh_problem(dest: &str, code: Option<i32>, stderr: &str) -> String {
    let host = dest.rsplit('@').next().unwrap_or(dest);
    let e = stderr.to_lowercase();
    if e.contains("permission denied") {
        format!("{host} wouldn't let this computer in without a password. Ombro signs in with your SSH key: set one up for it (ssh-copy-id {dest}), then try again.")
    } else if e.contains("could not resolve") || e.contains("name or service not known") {
        format!("No server called {host} could be found. Check the name.")
    } else if e.contains("host key verification failed") || e.contains("remote host identification has changed") {
        format!("{host}'s identity doesn't match the one this computer remembers. If the server was rebuilt, remove its line from ~/.ssh/known_hosts, then try again.")
    } else if code == Some(127) || e.contains("command not found") {
        format!("Ombro isn't installed on {host}. Install it there first (pipx install ombro), then try again.")
    } else if e.contains("timed out") || e.contains("connection refused") || e.contains("no route") {
        format!("{host} didn't answer. Check that it's on and reachable from this computer.")
    } else {
        let line = stderr.lines().rev().find(|l| !l.trim().is_empty()).unwrap_or("").trim();
        format!("Connecting to {host} over SSH didn't work{}", if line.is_empty() { ".".into() } else { format!(": {line}") })
    }
}

/// Pair over SSH: run `ombro pair --device <name>` on the server and keep the token it prints.
pub async fn pair_ssh(host: &str, port: u16) -> Result<String, String> {
    let name = device_name();
    // On another port, the server checks that the Ombro answering there is the one it pairs for
    // (not one in Docker, say), and says so if not.
    let on_port = if port == DEFAULT_PORT { String::new() } else { format!(" --port {port}") };
    // Non-interactive SSH sessions often lack ~/.local/bin, where pipx and uv put ombro.
    let remote = format!("PATH=\"$HOME/.local/bin:$PATH\" ombro pair --device '{name}'{on_port}");
    let mut cmd = Command::new(ssh_program());
    cmd.args(ssh_options()).arg(host).arg(remote).stdin(Stdio::null());
    let out = tokio::time::timeout(Duration::from_secs(40), cmd.output())
        .await
        .map_err(|_| format!("{host} didn't answer in time."))?
        .map_err(|e| format!("ssh couldn't be started: {e}"))?;
    if !out.status.success() {
        return Err(ssh_problem(host, out.status.code(), &String::from_utf8_lossy(&out.stderr)));
    }
    #[derive(Deserialize)]
    struct Paired {
        token: String,
    }
    let text = String::from_utf8_lossy(&out.stdout);
    let start = text.find('{').ok_or("the server's Ombro is too old to pair over SSH; update it")?;
    let paired: Paired = serde_json::from_str(&text[start..]).map_err(|_| "the server's answer wasn't what Ombro expected; update Ombro there".to_string())?;
    Ok(paired.token)
}

/// A free port on 127.0.0.1.
pub fn free_port() -> Result<u16, String> {
    let l = std::net::TcpListener::bind("127.0.0.1:0").map_err(|e| e.to_string())?;
    Ok(l.local_addr().map_err(|e| e.to_string())?.port())
}

async fn wait_for_port(port: u16, within: Duration) -> bool {
    let deadline = tokio::time::Instant::now() + within;
    while tokio::time::Instant::now() < deadline {
        if TcpStream::connect(("127.0.0.1", port)).await.is_ok() {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
    false
}

fn spawn_tunnel(host: &str, local: u16, remote: u16) -> Result<Child, String> {
    let mut cmd = Command::new(ssh_program());
    cmd.args(ssh_options())
        .args(["-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "-N", "-L"])
        .arg(format!("127.0.0.1:{local}:127.0.0.1:{remote}"))
        .arg(host)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    cmd.spawn().map_err(|e| format!("ssh couldn't be started: {e}"))
}

/// Start the SSH tunnel and keep it up. Returns once the local port answers (or with why not).
/// Where the running tunnel's process id is kept, so a tunnel left by an app that didn't quit
/// cleanly (it crashed, or was killed) can be stopped next time.
fn tunnel_pid_path(app: &tauri::AppHandle) -> Option<PathBuf> {
    config_path(app).ok().map(|p| p.with_file_name("tunnel.pid"))
}

fn remember_tunnel(app: &tauri::AppHandle, pid: u32) {
    if let Some(r) = app.try_state::<Remote>() {
        *r.ssh.lock().unwrap() = Some(pid);
    }
    if let Some(path) = tunnel_pid_path(app) {
        let _ = std::fs::write(path, pid.to_string());
    }
}

/// Stop a tunnel an earlier run of the app left behind: only an ssh process that is one of ours.
fn stop_stale_tunnel(app: &tauri::AppHandle) {
    let Some(path) = tunnel_pid_path(app) else { return };
    let Some(pid) = std::fs::read_to_string(&path).ok().and_then(|t| t.trim().parse::<u32>().ok()) else { return };
    let _ = std::fs::remove_file(&path);
    let cmd = std::process::Command::new("ps").args(["-p", &pid.to_string(), "-o", "command="]).output();
    if let Ok(out) = cmd {
        let line = String::from_utf8_lossy(&out.stdout);
        if line.contains("ssh") && line.contains("-N -L 127.0.0.1:") {
            let _ = std::process::Command::new("kill").arg(pid.to_string()).status();
        }
    }
}

pub async fn start_tunnel(app: tauri::AppHandle, host: String, remote: u16) -> Result<u16, String> {
    stop_stale_tunnel(&app);
    let local = free_port()?;
    let mut child = spawn_tunnel(&host, local, remote)?;
    if !wait_for_port(local, Duration::from_secs(20)).await {
        let _ = child.start_kill();
        let out = child.wait_with_output().await.map_err(|e| e.to_string())?;
        return Err(ssh_problem(&host, out.status.code(), &String::from_utf8_lossy(&out.stderr)));
    }
    tauri::async_runtime::spawn(async move {
        loop {
            if let Some(pid) = child.id() {
                remember_tunnel(&app, pid);
            }
            let _ = child.wait().await;
            eprintln!("[ssh] the tunnel to {host} closed; starting it again");
            loop {
                tokio::time::sleep(Duration::from_secs(3)).await;
                if let Ok(c) = spawn_tunnel(&host, local, remote) {
                    child = c;
                    break;
                }
            }
        }
    });
    Ok(local)
}

/// For pairing over SSH with a code (Ombro in Docker on the server, which the server's own
/// `ombro pair` can't reach): a tunnel to the server's port, kept only until pairing ends.
pub async fn pairing_tunnel(app: &tauri::AppHandle, host: &str, remote: u16) -> Result<u16, String> {
    end_pairing_tunnel(app);
    let local = free_port()?;
    let mut child = spawn_tunnel(host, local, remote)?;
    if !wait_for_port(local, Duration::from_secs(20)).await {
        let _ = child.start_kill();
        let out = child.wait_with_output().await.map_err(|e| e.to_string())?;
        return Err(ssh_problem(host, out.status.code(), &String::from_utf8_lossy(&out.stderr)));
    }
    if let Some(r) = app.try_state::<Remote>() {
        *r.pairing.lock().unwrap() = Some(child);
    }
    Ok(local)
}

pub fn end_pairing_tunnel(app: &tauri::AppHandle) {
    if let Some(r) = app.try_state::<Remote>() {
        if let Some(mut child) = r.pairing.lock().unwrap().take() {
            let _ = child.start_kill();
        }
    }
}

/// A forwarder from a local port to `address`, for as long as the app runs.
pub async fn start_forwarder(address: String) -> Result<u16, String> {
    let listener = TcpListener::bind(SocketAddr::from(([127, 0, 0, 1], 0))).await.map_err(|e| e.to_string())?;
    let port = listener.local_addr().map_err(|e| e.to_string())?.port();
    tauri::async_runtime::spawn(async move {
        loop {
            let Ok((mut inbound, _)) = listener.accept().await else { continue };
            let address = address.clone();
            tauri::async_runtime::spawn(async move {
                match tokio::time::timeout(Duration::from_secs(10), TcpStream::connect(&address)).await {
                    Ok(Ok(mut outbound)) => {
                        let _ = tokio::io::copy_bidirectional(&mut inbound, &mut outbound).await;
                    }
                    _ => eprintln!("[direct] {address} didn't answer"),
                }
            });
        }
    });
    Ok(port)
}

pub fn stop(app: &tauri::AppHandle) {
    end_pairing_tunnel(app);
    if let Some(r) = app.try_state::<Remote>() {
        if let Some(pid) = r.ssh.lock().unwrap().take() {
            let _ = std::process::Command::new("kill").arg(pid.to_string()).status();
        }
    }
    if let Some(path) = tunnel_pid_path(app) {
        let _ = std::fs::remove_file(path);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_are_plain() {
        assert_eq!(clean_name("Mark's MacBook Pro"), "Mark s MacBook Pro");
        assert_eq!(clean_name("box; rm -rf ~"), "box rm -rf");
        assert_eq!(clean_name("   "), "Ombro app");
        assert_eq!(clean_name("--help me"), "help me");
    }

    #[test]
    fn hosts_and_addresses() {
        assert_eq!(check_host(" mark@box.example.com ").unwrap(), "mark@box.example.com");
        assert!(check_host("-oProxyCommand=evil").is_err());
        assert!(check_host("box; rm").is_err());
        assert!(check_host("").is_err());
        assert_eq!(check_address("100.101.102.103").unwrap(), "100.101.102.103:8765");
        assert_eq!(check_address("http://127.0.0.1:9000/").unwrap(), "127.0.0.1:9000");
        assert_eq!(check_address("box.tail1234.ts.net").unwrap(), "box.tail1234.ts.net:8765");
        assert!(check_address("a b").is_err());
    }

    #[test]
    fn connections_round_trip() {
        let c = Connection::Ssh { host: "mark@box".into(), port: 8765 };
        let text = serde_json::to_string(&c).unwrap();
        assert_eq!(text, r#"{"mode":"ssh","host":"mark@box","port":8765}"#);
        assert_eq!(serde_json::from_str::<Connection>(&text).unwrap(), c);
        assert_eq!(c.account(), "ssh:mark@box:8765");
        assert_eq!(serde_json::to_string(&Connection::Local).unwrap(), r#"{"mode":"local"}"#);
    }

    #[test]
    fn ssh_problems_say_what_to_do() {
        assert!(ssh_problem("box", Some(255), "Permission denied (publickey).").contains("ssh-copy-id box"));
        assert!(ssh_problem("box", Some(127), "bash: ombro: command not found").contains("pipx install ombro"));
        assert!(ssh_problem("nope", Some(255), "ssh: Could not resolve hostname nope").contains("No server called nope"));
        assert!(ssh_problem("ada@nope", Some(255), "ssh: Could not resolve hostname nope").contains("No server called nope could"));
        assert!(ssh_problem("ada@box", Some(255), "Permission denied").contains("ssh-copy-id ada@box"));
    }
}
