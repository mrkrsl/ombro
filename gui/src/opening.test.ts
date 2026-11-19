// Security review M3 and L3: files that run code are only shown (the app says why), and the
// app's sign-in listener hands over only the answer that brings back the sign-in's state.
import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invoke(...a) }));
const reveal = vi.fn(async () => undefined);
vi.mock("@tauri-apps/plugin-opener", () => ({ revealItemInDir: reveal, openUrl: vi.fn(), openPath: vi.fn() }));
vi.mock("./api/serve", async (real) => ({ ...(await real<typeof import("./api/serve")>()), inTauri: true }));

const { catchSignIn, openLink, signInState } = await import("./platform");
const { useStore } = await import("./store/store");

beforeEach(() => {
  invoke.mockReset();
  useStore.setState({ remote: false, toasts: [] });
});

describe("opening a job's file", () => {
  it("asks the app, which opens it, and says nothing", async () => {
    invoke.mockResolvedValue(null);
    await useStore.getState().openPath("/home/jobs/j1/workspace/notes.md", "open");
    expect(invoke).toHaveBeenCalledWith("open_file", { path: "/home/jobs/j1/workspace/notes.md" });
    expect(useStore.getState().toasts).toEqual([]);
  });

  it("says why when the app only showed it in Finder", async () => {
    const why = "Shown in Finder, not opened: Summary.command could run code (a .command file runs code when it's opened).";
    invoke.mockResolvedValue(why);
    await useStore.getState().openPath("/home/jobs/j1/workspace/Summary.command", "open");
    expect(useStore.getState().toasts.map((t) => t.text)).toEqual([why]);
  });

  it("opens a file:// link the same way, never with the plain opener", async () => {
    invoke.mockResolvedValue(null);
    await openLink("file:///home/jobs/j1/workspace/caf%C3%A9.md");
    expect(invoke).toHaveBeenCalledWith("open_file", { path: "/home/jobs/j1/workspace/café.md" });
  });

  it("says why when the app won't open a linked file", async () => {
    invoke.mockRejectedValue("Ombro opens only files in its own folders and your shared folders, and /etc/hosts is in neither.");
    await useStore.getState().openHref("file:///etc/hosts");
    expect(useStore.getState().toasts.map((t) => t.tone)).toEqual(["error"]);
  });

  it("opens a job's folder through the app too", async () => {
    invoke.mockResolvedValue(null);
    await useStore.getState().openFolder("/home/jobs/j1");
    expect(invoke).toHaveBeenCalledWith("open_file", { path: "/home/jobs/j1" });
  });

  it("shows in Finder without asking the app", async () => {
    await useStore.getState().openPath("/home/jobs/j1/workspace/Summary.command", "reveal");
    expect(reveal).toHaveBeenCalledWith("/home/jobs/j1/workspace/Summary.command");
    expect(invoke).not.toHaveBeenCalled();
  });
});

describe("catching a sign-in's answer", () => {
  it("reads the state from the sign-in address", () => {
    expect(signInState("https://auth.example/authorize?client_id=a&state=s%2B1&redirect_uri=x")).toBe("s+1");
    expect(signInState("https://auth.example/authorize")).toBeNull();
    expect(signInState("not an address")).toBeNull();
  });

  it("waits for the answer with that state", async () => {
    invoke.mockResolvedValue("http://127.0.0.1:5000/callback?code=c&state=s1");
    const url = "https://auth.example/authorize?state=s1";
    expect(await catchSignIn(5000, url)).toBe("http://127.0.0.1:5000/callback?code=c&state=s1");
    expect(invoke).toHaveBeenCalledWith("sign_in_answer", { port: 5000, seconds: 600, state: "s1" });
  });

  it("prefers the state the server gave", async () => {
    invoke.mockResolvedValue(null);
    await catchSignIn(5000, "https://auth.example/authorize?state=from-url", "from-server");
    expect(invoke).toHaveBeenCalledWith("sign_in_answer", { port: 5000, seconds: 600, state: "from-server" });
  });

  it("doesn't wait when there's no state to check by", async () => {
    expect(await catchSignIn(5000, "https://auth.example/authorize")).toBeNull();
    expect(invoke).not.toHaveBeenCalled();
  });
});
