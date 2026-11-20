// Where the app runs: the window chrome and how links open depend on it.

import { inTauri } from "./api/serve";

/** macOS: the title bar is drawn over the window, with its buttons in the sidebar.
 * Linux: an ordinary title bar, with its buttons on the right. */
export const isMac = typeof navigator !== "undefined" && /Mac/.test(navigator.userAgent);

/** What the system's file manager is called, for "Show in …": Finder, or Files on Linux (GNOME's). */
export const fileManager = isMac ? "Finder" : "Files";

/** Quit the app (Ctrl+Q on Linux; macOS has Cmd+Q in its menu). */
export async function quitApp(): Promise<void> {
  if (!inTauri) return;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("quit");
}

/** Open a file (or folder) with the app the system uses for its kind (for a .md file, say
 * TextEdit). The app opens only files in Ombro's folders and the shared folders, and a kind that
 * runs code (a .command, an .app, a script…) is shown in Finder instead: then this returns the
 * line saying why, to show. */
export async function openFile(path: string): Promise<string | null> {
  if (!inTauri) throw new Error("Files open in the desktop app only.");
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<string | null>("open_file", { path });
}

/** Show a file in Finder (or the file manager), picked. */
export async function revealFile(path: string): Promise<void> {
  if (!inTauri) throw new Error("Files are shown in the desktop app only.");
  const opener = await import("@tauri-apps/plugin-opener");
  await opener.revealItemInDir(path);
}

/** Open a link in the system browser, or a file:// link as openFile does (returning its line, if
 * it was only shown). */
export async function openLink(href: string): Promise<string | null> {
  if (!inTauri) {
    window.open(href, "_blank", "noopener");
    return null;
  }
  if (href.startsWith("file:")) return openFile(decodeURIComponent(new URL(href).pathname));
  const opener = await import("@tauri-apps/plugin-opener");
  await opener.openUrl(href);
  return null;
}

/** The `state` a sign-in address carries (the answer must bring it back), or null. */
export function signInState(url: string): string | null {
  try {
    return new URL(url).searchParams.get("state");
  } catch {
    return null;
  }
}

/** Wait for the answer the app's listener on `port` catches for the sign-in at `url`: one that
 * brings back its `state`. Answers with any other state are passed over. null if none came, or
 * the address has no state to check by (then the address is pasted instead). App only. */
export async function catchSignIn(port: number, url: string, state?: string | null): Promise<string | null> {
  const want = state || signInState(url);
  if (!want) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<string | null>("sign_in_answer", { port, seconds: 600, state: want }).catch(() => null);
}

/** A system notification. On macOS the app posts it itself (notify.rs: macOS asks once for
 * permission); on Linux through the notification plugin; in a browser, the browser's own, where
 * allowed. */
export async function notify(title: string, body: string): Promise<void> {
  try {
    if (inTauri && isMac) {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("notify", { title, body });
      return;
    }
    if (inTauri) {
      const n = await import("@tauri-apps/plugin-notification");
      let ok = await n.isPermissionGranted();
      if (!ok) ok = (await n.requestPermission()) === "granted";
      if (ok) n.sendNotification({ title, body });
      return;
    }
    if (typeof Notification === "undefined") return;
    if (Notification.permission === "default") await Notification.requestPermission();
    if (Notification.permission === "granted") new Notification(title, { body });
  } catch {
    // A notification that can't be shown is not worth an error.
  }
}

/** Keep the menu bar item's lines and icon in step with the counts (app only). */
export async function setTray(counts: { active: number; scheduled: number; waiting: number; unseen: number; crews_ready: number }) {
  if (!inTauri) return;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("set_tray", { counts }).catch(() => undefined);
}

/** A line clicked in the menu bar item names a page to open (app only). */
export async function onTrayOpen(open: (screen: string) => void): Promise<void> {
  if (!inTauri) return;
  const { listen } = await import("@tauri-apps/api/event");
  await listen<string>("open-screen", (e) => open(e.payload));
}

// -- zoom: Cmd + and Cmd - (Ctrl on Linux) scale the whole window, Cmd 0 goes back --------------

const ZOOMS = [0.6, 0.7, 0.8, 0.85, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2];
/** The window starts a little smaller than the designs' pixel sizes. */
export const DEFAULT_ZOOM = 0.9;
const ZOOM_KEY = "ombro.zoom";

function savedZoom(): number {
  try {
    const z = Number(localStorage.getItem(ZOOM_KEY));
    return ZOOMS.includes(z) ? z : DEFAULT_ZOOM;
  } catch {
    return DEFAULT_ZOOM;
  }
}

async function applyZoom(level: number): Promise<void> {
  // The window buttons don't zoom with the page: CSS that sits beside them reads --zoom.
  document.documentElement.style.setProperty("--zoom", String(level));
  try {
    localStorage.setItem(ZOOM_KEY, String(level));
  } catch {
    // Not kept, then; it still applies.
  }
  const { getCurrentWebview } = await import("@tauri-apps/api/webview");
  await getCurrentWebview().setZoom(level).catch(() => undefined);
}

/** The next zoom level up or down from `level`, or back to the default (step 0). */
export function stepZoom(level: number, step: -1 | 0 | 1): number {
  if (step === 0) return DEFAULT_ZOOM;
  const i = ZOOMS.findIndex((z) => z >= level - 1e-6);
  const at = i < 0 ? ZOOMS.length - 1 : i;
  return ZOOMS[Math.min(Math.max(at + step, 0), ZOOMS.length - 1)];
}

/** Apply the kept zoom, and listen for the keys. Returns the clean-up. */
export function startZoom(): () => void {
  if (!inTauri) return () => undefined; // a browser has its own zoom
  let level = savedZoom();
  void applyZoom(level);
  function onKey(e: KeyboardEvent) {
    if (!(isMac ? e.metaKey : e.ctrlKey) || e.altKey) return;
    const step = e.key === "=" || e.key === "+" ? 1 : e.key === "-" || e.key === "_" ? -1 : e.key === "0" ? 0 : null;
    if (step === null) return;
    e.preventDefault();
    level = stepZoom(level, step);
    void applyZoom(level);
  }
  window.addEventListener("keydown", onKey);
  return () => window.removeEventListener("keydown", onKey);
}

/** Run `fn` once the window is in front of someone: now if it is showing, else when it next shows.
 * So a job counts as seen only when it could have been, and the menu bar's alert isn't cleared
 * by a window that is closed to the menu bar. */
export function whenVisible(fn: () => void): void {
  if (typeof document === "undefined" || document.visibilityState === "visible") {
    fn();
    return;
  }
  const once = () => {
    if (document.visibilityState !== "visible") return;
    document.removeEventListener("visibilitychange", once);
    fn();
  };
  document.addEventListener("visibilitychange", once);
}

/** The desktop app's version (from its bundle), or "" outside the app. */
export async function appVersion(): Promise<string> {
  if (!inTauri) return "";
  try {
    const { getVersion } = await import("@tauri-apps/api/app");
    return await getVersion();
  } catch {
    return "";
  }
}

/** A skill folder chosen with the system's picker, read on this computer (app only): its SKILL.md
 * and references, to send to serve, which may run elsewhere. null when the picker was closed. */
export type SkillFolder = { folder: string; path: string; files: Record<string, string>; skipped: string[] };
export async function readSkillFolder(): Promise<SkillFolder | null> {
  if (!inTauri) throw new Error("Adding a skill from a folder works in the desktop app.");
  const { open } = await import("@tauri-apps/plugin-dialog");
  const picked = await open({ directory: true, multiple: false, title: "Choose a skill's folder (the one with its SKILL.md)" });
  if (typeof picked !== "string") return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<SkillFolder>("read_skill_folder", { path: picked });
}
