//! The window's menu bar: the platform's standard menus, with Ombro's pages added to View
//! (Chat, Jobs, Approvals, Scheduled, Settings). A pick opens the page the way the menu bar
//! item does. The shortcuts match the web view's own. The app menu has "Check for Updates…",
//! which the web view answers (updates.rs), and under it "Install ‘ombro’ Command…" (command.rs).

use tauri::menu::{Menu, MenuItem, MenuItemKind, PredefinedMenuItem, Submenu};
use tauri::{AppHandle, Emitter, Manager, Runtime};

/// The pages View lists: (screen, label, shortcut).
pub const PAGES: &[(&str, &str, Option<&str>)] = &[
    ("chat", "Chat", None),
    ("jobs", "Jobs", Some("CmdOrCtrl+J")),
    ("approvals", "Approvals", Some("CmdOrCtrl+Shift+A")),
    ("scheduled", "Scheduled", Some("CmdOrCtrl+D")),
    ("settings", "Settings", Some("CmdOrCtrl+Comma")),
];

const PREFIX: &str = "view:";
const CHECK_UPDATES: &str = "app:check-updates";

/// The "Install ‘ombro’ Command…" item, kept so its words can change once it has run.
pub struct CommandItem<R: Runtime>(pub MenuItem<R>);

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
    // The app menu (the first, named after the app): About Ombro, then Check for Updates….
    if let Some(MenuItemKind::Submenu(app_menu)) = menu.items()?.into_iter().next() {
        let check = MenuItem::with_id(app, CHECK_UPDATES, "Check for Updates…", true, None::<&str>)?;
        let at = app_menu.items()?.len().min(1);
        app_menu.insert(&check, at)?;
        let command = MenuItem::with_id(app, crate::command::ID, crate::command::label(), true, None::<&str>)?;
        app_menu.insert(&command, at + 1)?;
        app.manage(CommandItem(command));
    }
    Ok(menu)
}

/// A pick from View: bring the window back (it may be hidden) on that page. Check for Updates…:
/// the window, and the web view looks. The ombro command: install or remove it, then reword the
/// item. Other menu events are left alone.
pub fn handle(app: &AppHandle, id: &str) {
    if let Some(screen) = id.strip_prefix(PREFIX) {
        crate::tray::open(app, screen);
    } else if id == CHECK_UPDATES {
        crate::tray::open(app, "current");
        let _ = app.emit("check-for-updates", ());
    } else if id == crate::command::ID {
        let app2 = app.clone();
        crate::command::pick(app, move |label| {
            if let Some(item) = app2.try_state::<CommandItem<tauri::Wry>>() {
                let _ = item.0.set_text(label);
            }
        });
    }
}
