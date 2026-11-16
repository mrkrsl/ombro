// Where the app runs: the window chrome and how links open depend on it.

import { inTauri } from "./api/serve";

/** macOS: the title bar is drawn over the window, with its buttons in the sidebar (design 01).
 * Linux: an ordinary title bar, with its buttons on the right (design 17). */
export const isMac = typeof navigator !== "undefined" && /Mac/.test(navigator.userAgent);

/** Open a file with the app the system uses for its kind (for a .md file, say TextEdit). */
export async function openFile(path: string): Promise<void> {
  if (!inTauri) throw new Error("Files open in the desktop app only.");
  const opener = await import("@tauri-apps/plugin-opener");
  await opener.openPath(path);
}

/** Show a file in Finder (or the file manager), picked. */
export async function revealFile(path: string): Promise<void> {
  if (!inTauri) throw new Error("Files are shown in the desktop app only.");
  const opener = await import("@tauri-apps/plugin-opener");
  await opener.revealItemInDir(path);
}

/** Open a link in the system browser, or a file:// link with the system's opener. */
export async function openLink(href: string): Promise<void> {
  if (!inTauri) {
    window.open(href, "_blank", "noopener");
    return;
  }
  const opener = await import("@tauri-apps/plugin-opener");
  if (href.startsWith("file:")) await opener.openPath(decodeURIComponent(new URL(href).pathname));
  else await opener.openUrl(href);
}

/** A system notification. In the app it asks once for permission; in a browser it uses the
 * browser's own, where allowed. */
export async function notify(title: string, body: string): Promise<void> {
  try {
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
