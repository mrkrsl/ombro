//! Updates to the app. A release puts a manifest, latest.json, on GitHub Releases: each platform's
//! file and its signature, made with Ombro's updater key, whose public half is in tauri.conf.json.
//! A download whose signature doesn't match is refused.
//!
//! The web view decides when to look (at start and daily, unless that's turned off, and from
//! "Check for Updates…") and asks before installing: it has these two commands, not the plugin.
//! Linux installs update in kind: an AppImage replaces itself, a .deb or .rpm asks for the
//! system's password (pkexec) to install the new package.

use std::sync::Mutex;

use serde::Serialize;
use tauri_plugin_updater::{Update, UpdaterExt};

/// The update found by the last check, waiting to be installed.
#[derive(Default)]
pub struct Pending(Mutex<Option<Update>>);

#[derive(Serialize)]
pub struct Found {
    version: String,
    notes: Option<String>,
}

/// Look for a newer release. None: this is the latest.
#[tauri::command]
pub async fn check_for_update(app: tauri::AppHandle, pending: tauri::State<'_, Pending>) -> Result<Option<Found>, String> {
    let update = app.updater().map_err(|e| e.to_string())?.check().await.map_err(|e| e.to_string())?;
    let found = update.as_ref().map(|u| Found { version: u.version.clone(), notes: u.body.clone() });
    *pending.0.lock().unwrap() = update;
    Ok(found)
}

/// Download and install the update the last check found, then start the new version.
#[tauri::command]
pub async fn install_update(app: tauri::AppHandle, pending: tauri::State<'_, Pending>) -> Result<(), String> {
    let update = pending.0.lock().unwrap().take().ok_or("no update is waiting: check again")?;
    update.download_and_install(|_, _| {}, || {}).await.map_err(|e| e.to_string())?;
    crate::stop_ombro(&app);
    app.restart();
}
