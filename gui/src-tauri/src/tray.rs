//! The menu bar item (design 18): a plain menu with Ombro, what waits for approval, and how
//! things stand. The icon changes when something needs you. The web view sends the counts
//! (`set_tray`); a click on a line opens the window at that page (the `open-screen` event).

use std::sync::Mutex;

use serde::Deserialize;
use tauri::image::Image;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{TrayIcon, TrayIconBuilder};
use tauri::{AppHandle, Emitter, Manager, Wry};

const IDLE: &[u8] = include_bytes!("../icons/tray/idle@2x.png");
const ALERT: &[u8] = include_bytes!("../icons/tray/alert@2x.png");

#[derive(Clone, Debug, Default, Deserialize, PartialEq)]
pub struct Counts {
    pub active: u32,
    pub scheduled: u32,
    pub waiting: u32,
    pub crews_ready: u32,
}

pub struct Tray {
    icon: TrayIcon,
    last: Mutex<Option<Counts>>,
}

fn plural(n: u32, one: &str, many: &str) -> String {
    format!("{n} {}", if n == 1 { one } else { many })
}

fn menu(app: &AppHandle, c: &Counts) -> tauri::Result<Menu<Wry>> {
    let approvals = if c.waiting == 0 {
        "Nothing to approve".to_string()
    } else {
        plural(c.waiting, "approval requested", "approvals requested")
    };
    Menu::with_items(
        app,
        &[
            &MenuItem::with_id(app, "open:home", "Ombro", true, None::<&str>)?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "open:approvals", approvals, true, None::<&str>)?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "open:home", plural(c.crews_ready, "crew ready to work", "crews ready to work"), true, None::<&str>)?,
            &MenuItem::with_id(app, "open:jobs", plural(c.active, "active job", "active jobs"), true, None::<&str>)?,
            &MenuItem::with_id(app, "open:scheduled", plural(c.scheduled, "scheduled job", "scheduled jobs"), true, None::<&str>)?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "quit", "Quit Ombro", true, None::<&str>)?,
        ],
    )
}

/// Bring the window back and ask it to show a page.
pub fn open(app: &AppHandle, screen: &str) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
    let _ = app.emit("open-screen", screen.to_string());
}

pub fn create(app: &AppHandle) -> tauri::Result<()> {
    let icon = TrayIconBuilder::with_id("ombro")
        .icon(Image::from_bytes(IDLE)?)
        .icon_as_template(true)
        .tooltip("Ombro")
        .menu(&menu(app, &Counts::default())?)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| {
            let id = event.id().as_ref();
            if id == "quit" {
                app.exit(0);
            } else if let Some(screen) = id.strip_prefix("open:") {
                open(app, screen);
            }
        })
        .build(app)?;
    app.manage(Tray { icon, last: Mutex::new(None) });
    Ok(())
}

/// The web view's counts, whenever they change: the menu's lines, and the icon when something waits.
#[tauri::command]
pub fn set_tray(app: AppHandle, tray: tauri::State<'_, Tray>, counts: Counts) -> Result<(), String> {
    let mut last = tray.last.lock().unwrap();
    if last.as_ref() == Some(&counts) {
        return Ok(());
    }
    let image = Image::from_bytes(if counts.waiting > 0 { ALERT } else { IDLE }).map_err(|e| e.to_string())?;
    tray.icon.set_icon(Some(image)).map_err(|e| e.to_string())?;
    let _ = tray.icon.set_icon_as_template(true);
    tray.icon.set_menu(Some(menu(&app, &counts).map_err(|e| e.to_string())?)).map_err(|e| e.to_string())?;
    let tip = if counts.waiting > 0 {
        plural(counts.waiting, "approval waiting", "approvals waiting")
    } else {
        "Ombro".to_string()
    };
    let _ = tray.icon.set_tooltip(Some(tip));
    *last = Some(counts);
    Ok(())
}
