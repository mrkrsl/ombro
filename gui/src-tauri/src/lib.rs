//! The shell: start `ombro serve` as a sidecar, read its one-line handshake,
//! and hand the port and token to the web view. Serve exits when its stdin
//! closes, so it never outlives the app; we also stop it on the way out.

use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use tauri::{Manager, RunEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use tokio::sync::watch;

#[derive(Clone, Debug, Serialize, Deserialize)]
struct Handshake {
    port: u16,
    token: String,
}

/// What the web view waits on: nothing yet, the handshake, or why serve failed.
type Ready = Option<Result<Handshake, String>>;

struct Serve {
    ready: watch::Receiver<Ready>,
    child: Mutex<Option<CommandChild>>,
}

#[tauri::command]
async fn handshake(serve: tauri::State<'_, Serve>) -> Result<Handshake, String> {
    let mut ready = serve.ready.clone();
    let got = ready
        .wait_for(|r| r.is_some())
        .await
        .map_err(|_| "ombro serve stopped before it was ready".to_string())?;
    got.clone().unwrap()
}

fn start_serve(app: &tauri::AppHandle) -> Result<(), String> {
    let (tx, rx) = watch::channel::<Ready>(None);
    let (mut events, child) = app
        .shell()
        .sidecar("ombro-serve")
        .map_err(|e| e.to_string())?
        .args(["serve", "--handshake"])
        .spawn()
        .map_err(|e| format!("could not start ombro serve: {e}"))?;
    app.manage(Serve { ready: rx, child: Mutex::new(Some(child)) });

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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            start_serve(app.handle())?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![handshake])
        .build(tauri::generate_context!())
        .expect("error while building the Ombro app")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(serve) = app.try_state::<Serve>() {
                    if let Some(child) = serve.child.lock().unwrap().take() {
                        let _ = child.kill();
                    }
                }
            }
        });
}
