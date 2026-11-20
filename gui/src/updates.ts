// Updates to the app (src-tauri/src/updates.rs fetches and checks them). Looked for a little after
// the app starts and once a day, unless that's turned off in Settings › General, and whenever
// "Check for Updates…" (the app menu) or Settings asks. Nothing installs without a click.

import { inTauri } from "./api/serve";
import { NOTICE_MS, useStore } from "./store/store";

const AUTO_KEY = "ombro.updates.auto";
const FIRST_LOOK_MS = 30_000; // after start, so the first look doesn't slow it
const DAY_MS = 24 * 60 * 60 * 1000;

type Found = { version: string; notes: string | null };

/** Whether to look by itself (on unless turned off; kept on this computer). */
export function autoCheck(): boolean {
  try {
    return localStorage.getItem(AUTO_KEY) !== "off";
  } catch {
    return true;
  }
}

export function setAutoCheck(on: boolean): void {
  try {
    localStorage.setItem(AUTO_KEY, on ? "on" : "off");
  } catch {
    // Not kept, then.
  }
}

async function call<T>(command: string): Promise<T> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(command);
}

function offer(version: string): void {
  useStore.getState().toast({
    id: "update",
    text: `Ombro ${version} is available.`,
    dismissable: true,
    action: { label: "Install and restart", run: () => void installUpdate(version) },
  });
}

/** Look now. `quiet` (the automatic look): say nothing unless an update is found. */
export async function checkForUpdate(quiet = false): Promise<void> {
  if (!inTauri) return;
  const st = useStore.getState();
  if (!quiet) st.toast({ id: "update", text: "Checking for updates…" });
  let found: Found | null;
  try {
    found = await call<Found | null>("check_for_update");
  } catch (e) {
    if (quiet) return;
    st.toast({ id: "update", text: `Couldn't check for updates: ${String(e)}`, tone: "error", dismissable: true });
    return;
  }
  if (found) offer(found.version);
  else if (!quiet) st.toast({ id: "update", text: "Ombro is up to date.", dismissable: true, ms: NOTICE_MS });
  else st.dismissToast("update");
}

/** Install the update found, and restart. Not while jobs run here: they run from the app. */
export async function installUpdate(version: string): Promise<void> {
  const st = useStore.getState();
  if (!st.remote && (st.counts?.active ?? 0) > 0) {
    st.toast({
      id: "update",
      text: `Ombro ${version} can be installed once the jobs running now have finished or been stopped.`,
      dismissable: true,
      action: { label: "Install and restart", run: () => void installUpdate(version) },
    });
    return;
  }
  st.toast({ id: "update", text: `Installing Ombro ${version}…` });
  try {
    await call("install_update"); // the app restarts as the new version
  } catch (e) {
    st.toast({ id: "update", text: `The update didn't install: ${String(e)}`, tone: "error", dismissable: true });
  }
}

/** Start looking: soon after start, then daily (unless turned off), and when the menu asks. */
export function startUpdates(): () => void {
  if (!inTauri) return () => undefined;
  const look = () => {
    if (autoCheck()) void checkForUpdate(true);
  };
  const first = setTimeout(look, FIRST_LOOK_MS);
  const daily = setInterval(look, DAY_MS);
  let unlisten: (() => void) | undefined;
  let gone = false;
  void import("@tauri-apps/api/event")
    .then(({ listen }) => listen("check-for-updates", () => void checkForUpdate()))
    .then((u) => (gone ? u() : (unlisten = u)));
  return () => {
    gone = true;
    clearTimeout(first);
    clearInterval(daily);
    unlisten?.();
  };
}
