// System notifications: a finished job, and approvals requested. One notification for one thing.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ServerMessage } from "../api/types";

const notify = vi.fn(async (_title: string, _body: string) => undefined);
vi.mock("../platform", async (orig) => ({ ...(await orig<typeof import("../platform")>()), notify }));

const { APPROVALS_NOTIFY_MS, useStore } = await import("./store");

const counts = (waiting: number) =>
  ({ type: "counts", active: 0, running: [], scheduled: 0, waiting, unseen: 0, crews_ready: 1 }) as ServerMessage;

const ended = (waiting: number) =>
  ({ type: "job", job_id: "job-1", name: "Invented teapots", status: "completed", line: "Your job has finished.",
     event: { type: "job_ended", job_id: "job-1", name: "Invented teapots", status: "completed",
              text: "Your job has finished.", waiting } }) as unknown as ServerMessage;

describe("notifications", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    notify.mockClear();
    useStore.setState({ api: null, counts: null, running: [], feed: [], chats: [], prefs: { notifications: true } as never });
    useStore.getState().receive(counts(0));
  });
  afterEach(() => vi.useRealTimers());

  it("says when an approval is requested", () => {
    useStore.getState().receive(counts(1));
    vi.advanceTimersByTime(APPROVALS_NOTIFY_MS);
    expect(notify).toHaveBeenCalledWith("Ombro", "An approval is requested in Approvals.");
    useStore.getState().receive(counts(3));
    vi.advanceTimersByTime(APPROVALS_NOTIFY_MS);
    expect(notify).toHaveBeenLastCalledWith("Ombro", "2 approvals are requested: 3 wait for you in Approvals.");
  });

  it("says it once when a job ends with approvals, whichever comes first", () => {
    useStore.getState().receive(counts(2));
    useStore.getState().receive(ended(2));
    vi.advanceTimersByTime(APPROVALS_NOTIFY_MS);
    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify).toHaveBeenCalledWith("Invented teapots", "Your job has finished. 2 waiting for your approval.");
  });

  it("says nothing when approved already, or when notifications are off", () => {
    useStore.getState().receive(counts(1));
    useStore.getState().receive(counts(0));
    vi.advanceTimersByTime(APPROVALS_NOTIFY_MS);
    useStore.setState({ prefs: { notifications: false } as never });
    useStore.getState().receive(counts(1));
    vi.advanceTimersByTime(APPROVALS_NOTIFY_MS);
    expect(notify).not.toHaveBeenCalled();
  });
});
