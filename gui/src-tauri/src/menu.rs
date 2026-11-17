//! The window's menu bar: the platform's standard menus, with Ombro's pages added to View
//! (Chat, Jobs, Approvals, Scheduled, Settings). A pick opens the page the way the menu bar
//! item does. The shortcuts match the web view's own.

use tauri::menu::{Menu, MenuItem, MenuItemKind, PredefinedMenuItem, Submenu};
use tauri::{AppHandle, Runtime};

/// The pages View lists: (screen, label, shortcut).
pub const PAGES: &[(&str, &str, Option<&str>)] = &[
    ("chat", "Chat", None),
    ("jobs", "Jobs", Some("CmdOrCtrl+J")),
    ("approvals", "Approvals", Some("CmdOrCtrl+Shift+A")),
    ("scheduled", "Scheduled", Some("CmdOrCtrl+D")),
    ("settings", "Settings", Some("CmdOrCtrl+Comma")),
];

const PREFIX: &str = "view:";

pub fn build<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<Menu<R>> {
    let menu = Menu::default(app)?;
    let pages = PAGES
        .iter()
        .map(|(screen, label, keys)| MenuItem::with_id(app, format!("{PREFIX}{screen}"), *label, true, *keys))
        .collect::<tauri::Result<Vec<_>>>()?;
    let view = menu.items()?.into_iter().find_map(|item| match item {
        MenuItemKind::Submenu(s) if s.text().map(|t| t == "View").unwrap_or(false) => Some(s),
        _ => None,
    });
    let view = match view {
        Some(v) => v,
        None => {
            let v = Submenu::with_id(app, "view", "View", true)?;
            // After App, File and Edit where they exist; at the end otherwise.
            let at = menu.items()?.len().min(3);
            menu.insert(&v, at)?;
            v
        }
    };
    let had = view.items()?.len();
    for (i, page) in pages.iter().enumerate() {
        view.insert(page, i)?;
    }
    if had > 0 {
        view.insert(&PredefinedMenuItem::separator(app)?, pages.len())?;
    }
    Ok(menu)
}

/// A pick from View: bring the window back (it may be hidden) on that page. Other menu events
/// are left alone.
pub fn handle(app: &AppHandle, id: &str) {
    if let Some(screen) = id.strip_prefix(PREFIX) {
        crate::tray::open(app, screen);
    }
}
