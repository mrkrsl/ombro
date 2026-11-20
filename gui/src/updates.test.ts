// Updates: found ones are offered with a button, nothing installs while jobs run here, and the
// automatic look says nothing unless it finds one.
import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn();
// A localStorage of the test's own (the test environment's doesn't keep anything).
const kept = new Map<string, string>();
vi.stubGlobal("localStorage", {
  getItem: (k: string) => kept.get(k) ?? null,
  setItem: (k: string, v: string) => void kept.set(k, v),
  removeItem: (k: string) => void kept.delete(k),
  clear: () => kept.clear(),
});
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invoke(...a) }));
vi.mock("./api/serve", async (real) => ({ ...(await real<typeof import("./api/serve")>()), inTauri: true }));

const { autoCheck, checkForUpdate, installUpdate, setAutoCheck } = await import("./updates");
const { useStore } = await import("./store/store");

const counts = (active: number) => ({ type: "counts" as const, active, crews_ready: 1, running: [], scheduled: 0, unseen: 0, waiting: 0 });
const toast = () => useStore.getState().toasts.find((t) => t.id === "update");

beforeEach(() => {
  invoke.mockReset();
  kept.clear();
  useStore.setState({ remote: false, toasts: [], counts: counts(0) });
});

describe("looking for an update", () => {
  it("offers one it finds, with a button", async () => {
    invoke.mockResolvedValue({ version: "0.2.1", notes: null });
    await checkForUpdate(true);
    expect(invoke).toHaveBeenCalledWith("check_for_update");
    expect(toast()?.text).toBe("Ombro 0.2.1 is available.");
    expect(toast()?.action?.label).toBe("Install and restart");
  });

  it("says nothing when the automatic look finds none or can't reach GitHub", async () => {
    invoke.mockResolvedValue(null);
    await checkForUpdate(true);
    invoke.mockRejectedValue("could not fetch a valid release JSON");
    await checkForUpdate(true);
    expect(toast()).toBeUndefined();
  });

  it("says so when asked and there is none, or it couldn't look", async () => {
    invoke.mockResolvedValue(null);
    await checkForUpdate();
    expect(toast()?.text).toBe("Ombro is up to date.");
    invoke.mockRejectedValue("offline");
    await checkForUpdate();
    expect(toast()).toMatchObject({ text: "Couldn't check for updates: offline", tone: "error" });
  });

  it("is on unless turned off", () => {
    expect(autoCheck()).toBe(true);
    setAutoCheck(false);
    expect(autoCheck()).toBe(false);
  });
});

describe("installing it", () => {
  it("installs (and the app restarts)", async () => {
    invoke.mockResolvedValue(undefined);
    await installUpdate("0.2.1");
    expect(invoke).toHaveBeenCalledWith("install_update");
  });

  it("waits while jobs run on this computer", async () => {
    useStore.setState({ counts: counts(2) });
    await installUpdate("0.2.1");
    expect(invoke).not.toHaveBeenCalled();
    expect(toast()?.text).toMatch(/once the jobs running now have finished/);
  });

  it("doesn't wait for a server's jobs: they don't run from this app", async () => {
    useStore.setState({ remote: true, counts: counts(2) });
    invoke.mockResolvedValue(undefined);
    await installUpdate("0.2.1");
    expect(invoke).toHaveBeenCalledWith("install_update");
  });

  it("says why when it didn't install", async () => {
    invoke.mockRejectedValue("signature mismatch");
    await installUpdate("0.2.1");
    expect(toast()).toMatchObject({ text: "The update didn't install: signature mismatch", tone: "error" });
  });
});
