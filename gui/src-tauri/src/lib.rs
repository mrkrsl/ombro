//! The shell. Ombro runs on this computer (start `ombro serve` as a sidecar, read its one-line
//! handshake) or on a server (connection.rs: an SSH tunnel or a forwarder, and the server's token
//! from the keychain). Either way the web view gets a port on 127.0.0.1 and a token. The sidecar
//! exits when its stdin closes, so it never outlives the app; we also stop it on the way out.

mod connection;
#[cfg(target_os = "macos")]
mod menu;
mod notify;
mod opening;
mod saved;
mod signin;
mod skillfolder;
mod tray;
mod updates;

use std::sync::Mutex;

use connection::{Connection, Remote};
use serde::{Deserialize, Serialize};
use tauri::{Manager, RunEvent, WindowEvent};
use tauri_plugin_window_state::StateFlags;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use tokio::sync::watch;

#[derive(Clone, Debug, Serialize, Deserialize)]
struct Handshake {
    port: u16,
    token: String,
    #[serde(default)]
    remote: bool,
}

/// What the web view waits on: nothing yet, the handshake, or why it failed.
type Ready = Option<Result<Handshake, String>>;

struct Serve {
    tx: watch::Sender<Ready>,
    ready: watch::Receiver<Ready>,
    child: Mutex<Option<CommandChild>>,
    /// Which connection was started, if any (only one per run of the app).
    started: Mutex<Option<Connection>>,
}

#[tauri::command]
async fn handshake(serve: tauri::State<'_, Serve>) -> Result<Handshake, String> {
    let mut ready = serve.ready.clone();
    let got = ready
        .wait_for(|r| r.is_some())
        .await
        .map_err(|_| "Ombro stopped before it was ready".to_string())?;
    got.clone().unwrap()
}

fn start_serve(app: &tauri::AppHandle) -> Result<(), String> {
    let serve = app.state::<Serve>();
    let tx = serve.tx.clone();
    let (mut events, child) = app
        .shell()
        .sidecar("ombro-serve")
        .map_err(|e| e.to_string())?
        .args(["serve", "--handshake"])
        .spawn()
        .map_err(|e| format!("could not start ombro serve: {e}"))?;
    *serve.child.lock().unwrap() = Some(child);

    tauri::async_runtime::spawn(async move {
        let mut line = Vec::new();
        while let Some(event) = events.recv().await {
            match event {
                CommandEvent::Stdout(bytes) if tx.borrow().is_none() => {
                    // The handshake is the first line; it may arrive in pieces.
                    line.extend_from_slice(&bytes);
                    if let Some(end) = line.iter().position(|b| *b == b'\n') {
                        let parsed = serde_json::from_slice::<Handshake>(&line[..end])
                            .map_err(|e| format!("ombro serve sent a bad handshake: {e}"));
                        let _ = tx.send(Some(parsed));
                    }
                }
                CommandEvent::Stderr(bytes) => eprint!("[serve] {}", String::from_utf8_lossy(&bytes)),
                CommandEvent::Error(e) => eprintln!("[serve] {e}"),
                CommandEvent::Terminated(status) => {
                    eprintln!("[serve] exited: {:?}", status.code);
                    tx.send_if_modified(|r| {
                        if r.is_none() {
                            *r = Some(Err(format!("ombro serve exited ({:?})", status.code)));
                            true
                        } else {
                            false
                        }
                    });
                    break;
                }
                _ => {}
            }
        }
    });
    Ok(())
}

/// Start the connection, once: the sidecar, or the tunnel/forwarder to a server. The handshake
/// (or the reason it failed) follows when it's up.
fn begin(app: &tauri::AppHandle, conn: Connection) -> Result<(), String> {
    let serve = app.state::<Serve>();
    {
        let mut started = serve.started.lock().unwrap();
        if started.is_some() {
            return Err("already started".into());
        }
        *started = Some(conn.clone());
    }
    if conn == Connection::Local {
        if let Err(e) = start_serve(app) {
            let _ = serve.tx.send(Some(Err(e.clone())));
            return Err(e);
        }
        return Ok(());
    }
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let result = async {
            let token = connection::token(&conn)?.ok_or_else(|| "This computer isn't paired with the server yet.".to_string())?;
            let port = match &conn {
                Connection::Ssh { host, port } => connection::start_tunnel(app.clone(), host.clone(), *port).await?,
                Connection::Direct { address } => connection::start_forwarder(address.clone()).await?,
                Connection::Local => unreachable!(),
            };
            Ok::<Handshake, String>(Handshake { port, token, remote: true })
        }
        .await;
        let _ = app.state::<Serve>().tx.send(Some(result));
    });
    Ok(())
}

#[tauri::command]
fn connection_state(app: tauri::AppHandle, serve: tauri::State<'_, Serve>) -> connection::State {
    let started = serve.started.lock().unwrap().clone();
    let conn = started.clone().or_else(|| connection::load(&app));
    connection::state(conn.as_ref(), started.is_some())
}

#[tauri::command]
fn device_name() -> String {
    connection::device_name()
}

/// Use Ombro on this computer from now on.
#[tauri::command]
fn use_this_computer(app: tauri::AppHandle) -> Result<(), String> {
    connection::save(&app, &Connection::Local)
}

/// Pair with a server over SSH and keep it as the connection. Doesn't start it.
#[tauri::command]
async fn pair_over_ssh(app: tauri::AppHandle, host: String, port: Option<u16>) -> Result<(), String> {
    let host = connection::check_host(&host)?;
    let conn = Connection::Ssh { host: host.clone(), port: port.unwrap_or(connection::DEFAULT_PORT) };
    let token = connection::pair_ssh(&host, port.unwrap_or(connection::DEFAULT_PORT)).await?;
    connection::keep_token(&conn, &token)?;
    connection::save(&app, &conn)
}

/// Pairing over SSH with a code (Ombro in Docker on the server): a short-lived tunnel to the
/// server's port, so the web view can post the code to 127.0.0.1:<port>/pair. Returns the port.
#[tauri::command]
async fn reach_over_ssh(app: tauri::AppHandle, host: String, port: Option<u16>) -> Result<u16, String> {
    let host = connection::check_host(&host)?;
    connection::pairing_tunnel(&app, &host, port.unwrap_or(connection::DEFAULT_PORT)).await
}

/// Keep a server reached over SSH, and the token pairing with a code gave, as the connection.
#[tauri::command]
fn keep_ssh(app: tauri::AppHandle, host: String, port: Option<u16>, token: String) -> Result<(), String> {
    connection::end_pairing_tunnel(&app);
    let conn = Connection::Ssh { host: connection::check_host(&host)?, port: port.unwrap_or(connection::DEFAULT_PORT) };
    connection::keep_token(&conn, &token)?;
    connection::save(&app, &conn)
}

/// Pairing over SSH with a code didn't go through: close its tunnel.
#[tauri::command]
fn end_ssh_pairing(app: tauri::AppHandle) {
    connection::end_pairing_tunnel(&app);
}

/// A forwarder to an address, for pairing with a code: the web view posts the code to
/// 127.0.0.1:<port>/pair. Returns the port, and the address as it will be kept.
#[tauri::command]
async fn reach_address(address: String) -> Result<(u16, String), String> {
    let address = connection::check_address(&address)?;
    let port = connection::start_forwarder(address.clone()).await?;
    Ok((port, address))
}

/// Keep a server reached by address, and the token pairing gave, as the connection.
#[tauri::command]
fn keep_address(app: tauri::AppHandle, address: String, token: String) -> Result<(), String> {
    let conn = Connection::Direct { address: connection::check_address(&address)? };
    connection::keep_token(&conn, &token)?;
    connection::save(&app, &conn)
}

/// Start the kept connection now (first run), or restart the app to use it (it's already running one).
#[tauri::command]
fn start_connection(app: tauri::AppHandle) -> Result<(), String> {
    let conn = connection::load(&app).ok_or("no connection is chosen yet")?;
    match begin(&app, conn) {
        Err(e) if e == "already started" => {
            connection::stop(&app);
            app.restart();
        }
        other => other,
    }
}

/// Quit Ombro: Ctrl+Q on Linux, where there's no app menu to carry it (macOS's Cmd+Q is in the menu).
#[tauri::command]
fn quit(app: tauri::AppHandle) {
    app.exit(0);
}

/// The very first time Ombro opens (no size kept from before): most of the screen, not the config's
/// fixed size, which looks small on a large display. After that the window-state plugin restores it.
fn first_window_size(app: &tauri::AppHandle) {
    use tauri_plugin_window_state::AppHandleExt;

    let Some(window) = app.get_webview_window("main") else { return };
    let kept = app
        .path()
        .app_config_dir()
        .map(|dir| dir.join(app.filename()))
        .ok()
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).ok())
        .is_some_and(|v| v.get("main").is_some());
    if kept {
        return;
    }
    let Ok(Some(monitor)) = window.current_monitor() else { return };
    let scale = monitor.scale_factor();
    let area = monitor.work_area().size.to_logical::<f64>(scale);
    // 80% of the width and 85% of the height, up to 1600 by 1100, and never under the minimum.
    let width = (area.width * 0.8).min(1600.0).max(900.0);
    let height = (area.height * 0.85).min(1100.0).max(520.0);
    let _ = window.set_size(tauri::LogicalSize::new(width, height));
    let _ = window.center();
}

/// Stop what this app started: the sidecar, or the tunnel to a server. On the way out, and before
/// an update restarts the app.
pub(crate) fn stop_ombro(app: &tauri::AppHandle) {
    if let Some(serve) = app.try_state::<Serve>() {
        if let Some(child) = serve.child.lock().unwrap().take() {
            let _ = child.kill();
        }
    }
    connection::stop(app);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default();
    // Linux: opening Ombro while it runs (from the dock or the app grid, with its window closed to
    // the tray) shows that window instead of starting a second app. It goes first, so the second
    // copy hands over and stops before it starts anything.
    #[cfg(target_os = "linux")]
    let builder = builder.plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| tray::open(app, "current")));
    // The menu bar is macOS's. On Linux it would be a row of menus inside the window, which the
    // design doesn't have; the shortcuts work without it (the web view handles them).
    #[cfg(target_os = "macos")]
    let builder = builder
        .menu(|app| menu::build(app))
        .on_menu_event(|app, event| menu::handle(app, event.id().as_ref()));
    builder
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        // Size, place and maximised, kept when the window closes and when Ombro quits. Not whether
        // it was showing: Ombro always opens with its window.
        .plugin(
            tauri_plugin_window_state::Builder::new()
                .with_state_flags(StateFlags::SIZE | StateFlags::POSITION | StateFlags::MAXIMIZED)
                .build(),
        )
        .setup(|app| {
            first_window_size(app.handle());
            let (tx, rx) = watch::channel::<Ready>(None);
            app.manage(Serve { tx, ready: rx, child: Mutex::new(None), started: Mutex::new(None) });
            app.manage(Remote::default());
            app.manage(signin::Catches::default());
            app.manage(updates::Pending::default());
            // Notifications are on unless the person says no: macOS asks on the first start.
            notify::ask_at_start();
            // A fresh install waits for the web view to ask where Ombro runs.
            if let Some(conn) = connection::at_start(app.handle()) {
                let _ = begin(app.handle(), conn);
            }
            // A Linux desktop without a tray (no AppIndicator support) still runs Ombro: the
            // window closes to nowhere, and opening Ombro again brings it back.
            if let Err(e) = tray::create(app.handle()) {
                if cfg!(target_os = "macos") {
                    return Err(e.into());
                }
                eprintln!("no tray icon: {e}");
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing the window hides it: Ombro stays in the menu bar (macOS) or the tray (Linux). Quit
            // from there, or Cmd+Q (Ctrl+Q on Linux).
            // Without a tray there would be no way back to a hidden window, so it closes and Ombro quits.
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.app_handle().try_state::<tray::Tray>().is_some() {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            handshake,
            tray::set_tray,
            notify::notify,
            opening::open_file,
            saved::save_download,
            saved::read_saved,
            connection_state,
            device_name,
            use_this_computer,
            pair_over_ssh,
            reach_over_ssh,
            keep_ssh,
            end_ssh_pairing,
            reach_address,
            keep_address,
            start_connection,
            skillfolder::read_skill_folder,
            signin::sign_in_listener,
            signin::sign_in_answer,
            updates::check_for_update,
            updates::install_update,
            quit
        ])
        .build(tauri::generate_context!())
        .expect("error while building the Ombro app")
        .run(|app, event| match event {
            RunEvent::Exit => stop_ombro(app),
            // macOS: a click on the Dock icon brings the hidden window back.
            #[cfg(target_os = "macos")]
            RunEvent::Reopen { .. } => tray::open(app, "current"),
            _ => {}
        });
}
