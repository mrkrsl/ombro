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
