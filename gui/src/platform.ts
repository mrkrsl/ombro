// Where the app runs: the window chrome and how links open depend on it.

import { inTauri } from "./api/serve";

/** macOS: the title bar is drawn over the window, with its buttons in the sidebar (design 01).
 * Linux: an ordinary title bar, with its buttons on the right (design 17). */
export const isMac = typeof navigator !== "undefined" && /Mac/.test(navigator.userAgent);

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
export async function setTray(counts: { active: number; scheduled: number; waiting: number; crews_ready: number }) {
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
