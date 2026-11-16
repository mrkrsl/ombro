// Keyboard shortcuts: Cmd on macOS, Ctrl elsewhere. Fixed, not set by the user.

export type ShortcutAction =
  | "new"
  | "sidebar"
  | "inspector"
  | "settings"
  | "jobs"
  | "approvals"
  | "scheduled"
  | "commands";

type Keys = { key: string; metaKey: boolean; ctrlKey: boolean; shiftKey: boolean; altKey: boolean };

/** Each shortcut: its key (as KeyboardEvent.key, lower case), whether Shift is held, and what it does. */
export const SHORTCUTS: { key: string; shift: boolean; action: ShortcutAction; label: string }[] = [
  { key: "n", shift: false, action: "new", label: "New chat" },
  { key: "b", shift: false, action: "sidebar", label: "Show or hide the sidebar" },
  { key: "i", shift: false, action: "inspector", label: "Show or hide the side panel" },
  { key: ",", shift: false, action: "settings", label: "Settings" },
  { key: "j", shift: false, action: "jobs", label: "Jobs" },
  // Cmd A stays Select All in the input box.
  { key: "a", shift: true, action: "approvals", label: "Approvals" },
  { key: "d", shift: false, action: "scheduled", label: "Scheduled" },
  { key: "/", shift: false, action: "commands", label: "Commands (the / menu)" },
];

/** The shortcut a key press names, or null. Cmd ? (Shift /) counts as Cmd /. */
export function shortcutFor(e: Keys, mac: boolean): ShortcutAction | null {
  if (!(mac ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey) || e.altKey) return null;
  const key = e.key === "?" ? "/" : e.key.toLowerCase();
  const hit = SHORTCUTS.find((s) => s.key === key && (s.shift === e.shiftKey || s.key === "/"));
  return hit ? hit.action : null;
}

/** How a shortcut is written for this platform: ⌘⇧A, or Ctrl+Shift+A. */
export function shortcutText(key: string, shift: boolean, mac: boolean): string {
  const k = key.length === 1 ? key.toUpperCase() : key;
  return mac ? `⌘${shift ? "⇧" : ""}${k}` : `Ctrl+${shift ? "Shift+" : ""}${k}`;
}
