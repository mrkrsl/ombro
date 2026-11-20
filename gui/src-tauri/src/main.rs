// Keeps a console window from opening alongside the app on Windows in release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // `<app> --ombro <args>`: Ombro's command line, not a window. An AppImage's jobs and its
    // scheduler entry run the .AppImage file this way (src/ombro/appimage.py), since the sidecar's
    // own path is in the AppImage's mount, which goes when the app quits. Before anything else,
    // so it works with no display (from cron) and never meets the single-instance check.
    let mut args = std::env::args_os().skip(1);
    if args.next().is_some_and(|a| a == "--ombro") {
        hand_to_sidecar(args.collect());
    }
    ombro_gui_lib::run()
}

/// Become the sidecar (ombro-serve, next to this binary) with these arguments. Doesn't return.
fn hand_to_sidecar(args: Vec<std::ffi::OsString>) -> ! {
    let sidecar = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(|dir| dir.join("ombro-serve")))
        .filter(|path| path.is_file());
    let Some(sidecar) = sidecar else {
        eprintln!("ombro: the app's command line (ombro-serve) isn't next to the app");
        std::process::exit(127);
    };
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        let err = std::process::Command::new(&sidecar).args(&args).exec();
        eprintln!("ombro: couldn't start {}: {err}", sidecar.display());
        std::process::exit(126);
    }
    #[cfg(not(unix))]
    {
        let status = std::process::Command::new(&sidecar).args(&args).status();
        std::process::exit(status.ok().and_then(|s| s.code()).unwrap_or(126));
    }
}
