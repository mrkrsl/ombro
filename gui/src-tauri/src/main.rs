// Keeps a console window from opening alongside the app on Windows in release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    ombro_gui_lib::run()
}
