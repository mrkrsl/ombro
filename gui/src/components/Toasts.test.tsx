// Toasts in the corner: timed, dismissable, both, or persistent; and "Restarting…" before a switch.
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const started = vi.hoisted(() => ({ calls: 0 }));
vi.mock("../where", async (real) => ({
  ...(await real<typeof import("../where")>()),
  startConnection: async () => {
    started.calls += 1;
    return new Promise<void>(() => undefined); // the app restarts: this never comes back
  },
}));

import { NOTICE_MS, RESTART_PAUSE_MS, useStore } from "../store/store";
import { Restarting, Toasts } from "./Toasts";

beforeEach(() => {
  useStore.setState({ toasts: [], restarting: false });
  started.calls = 0;
});
afterEach(() => vi.useRealTimers());

describe("toasts", () => {
  it("clears a timed notice by itself", () => {
    vi.useFakeTimers();
    render(<Toasts />);
    act(() => useStore.getState().say("That job made no files."));
    expect(screen.getByRole("status")).toHaveTextContent("That job made no files.");
    act(() => vi.advanceTimersByTime(NOTICE_MS + 10));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("keeps a problem until it is dismissed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<Toasts />);
    act(() => useStore.getState().say("The server didn't answer.", "error"));
    act(() => vi.advanceTimersByTime(NOTICE_MS * 3));
    expect(screen.getByRole("alert")).toHaveTextContent("The server didn't answer.");
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows one notice at a time, and the next thing you do clears it", () => {
    render(<Toasts />);
    act(() => useStore.getState().say("First.", "error"));
    act(() => useStore.getState().say("Second.", "error"));
    expect(screen.getAllByRole("alert").map((t) => t.textContent)).toEqual(["Second."]);
    act(() => useStore.getState().say(""));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps a persistent toast, with no Dismiss, until what showed it takes it away", () => {
    vi.useFakeTimers();
    render(<Toasts />);
    act(() => useStore.getState().toast({ id: "lost", text: "Reconnecting to Ombro…" }));
    act(() => useStore.getState().say("Saved."));
    act(() => vi.advanceTimersByTime(60_000));
    expect(screen.getByRole("status")).toHaveTextContent("Reconnecting to Ombro…");
    expect(screen.queryByRole("button", { name: "Dismiss" })).toBeNull();
    act(() => useStore.getState().dismissToast("lost"));
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("switching where Ombro runs", () => {
  it("says it's restarting before it restarts", async () => {
    vi.useFakeTimers();
    useStore.setState({ where: { mode: "local", host: null, port: null, address: null, local_home: true, started: true } });
    void useStore.getState().connectNow();
    expect(useStore.getState().restarting).toBe(true);
    render(<Restarting />);
    expect(screen.getByRole("status")).toHaveTextContent("Restarting…");
    expect(started.calls).toBe(0);
    await act(async () => vi.advanceTimersByTimeAsync(RESTART_PAUSE_MS + 10));
    expect(started.calls).toBe(1);
  });

  it("doesn't on a first start, which has nothing to restart", async () => {
    useStore.setState({ where: { mode: "local", host: null, port: null, address: null, local_home: false, started: false } });
    void useStore.getState().connectNow();
    await act(async () => undefined);
    expect(useStore.getState().restarting).toBe(false);
    expect(started.calls).toBe(1);
  });
});
