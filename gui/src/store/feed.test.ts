// Job status in the panels: the sidebar's ring from the moment a job starts, and the side panel's
// background work, timed, letting go of a finished job a minute on.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatRow, ServerMessage } from "../api/types";
import { stamp } from "../components/Inspector";
import { FEED_CLEAR_MS, useStore } from "./store";

const row = (name: string, jobs: string[]): ChatRow =>
  ({ name, title: name, path: `/x/${name}.md`, updated_at: "", when: "", id: null, jobs, pinned: false });

const counts = (running: string[]) =>
  ({ type: "counts", active: running.length, running, scheduled: 0, waiting: 0, unseen: 0, crews_ready: 1 }) as ServerMessage;

const step = (job: string, line: string) =>
  ({ type: "job", job_id: job, name: "Invented teapots", status: "running", line,
     event: { type: "job_progress", job_id: job, name: "Invented teapots", role: "researcher", action: "search",
              note: line, line, live: false } }) as unknown as ServerMessage;

const ended = (job: string) =>
  ({ type: "job", job_id: job, name: "Invented teapots", status: "completed", line: "Your job has finished.",
     event: { type: "job_ended", job_id: job, name: "Invented teapots", status: "completed",
              text: "Your job has finished.", waiting: 0 } }) as unknown as ServerMessage;

describe("job status", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useStore.setState({ api: null, counts: null, running: [], feed: [], chats: [row("Teapots", ["job-1"])],
      prefs: { notifications: false } as never });
  });
  afterEach(() => vi.useRealTimers());

  it("knows a job is running from the counts, before any step", () => {
    useStore.getState().receive(counts(["job-1"]));
    expect(useStore.getState().running).toEqual(["job-1"]);
    useStore.getState().receive(counts([]));
    expect(useStore.getState().running).toEqual([]);
  });

  it("asks for the chats when a running job is in none of them yet", () => {
    const get = vi.fn(async () => [row("Teapots", ["job-1", "job-2"])]);
    useStore.setState({ api: { get } as never });
    useStore.getState().receive(counts(["job-1"]));
    expect(get).not.toHaveBeenCalled();
    useStore.getState().receive(counts(["job-1", "job-2"]));
    expect(get).toHaveBeenCalledWith("/chats");
  });

  it("clears a finished job's lines a minute on, and only that job's", () => {
    const r = useStore.getState().receive;
    r(step("job-1", "The researcher searches."));
    r(step("job-2", "The writer drafts."));
    r(ended("job-1"));
    expect(useStore.getState().feed).toHaveLength(3);
    vi.advanceTimersByTime(FEED_CLEAR_MS - 1);
    expect(useStore.getState().feed).toHaveLength(3);
    vi.advanceTimersByTime(1);
    expect(useStore.getState().feed.map((f) => f.jobId)).toEqual(["job-2"]);
  });

  it("keeps the lines of a job carried on before the minute is up", () => {
    const r = useStore.getState().receive;
    r(step("job-1", "The researcher searches."));
    r(ended("job-1"));
    r(step("job-1", "The researcher carries on."));
    vi.advanceTimersByTime(FEED_CLEAR_MS);
    expect(useStore.getState().feed.map((f) => f.line)).toEqual(
      ["The researcher searches.", "Your job has finished.", "The researcher carries on."]);
  });

  it("times each line as 10:28 on 19 November", () => {
    vi.setSystemTime(new Date(2026, 10, 19, 10, 28));
    useStore.getState().receive(step("job-1", "The researcher searches."));
    expect(stamp(useStore.getState().feed[0].at)).toBe("10:28 on 19 November");
    expect(stamp(new Date(2026, 0, 2, 9, 5).getTime())).toBe("09:05 on 2 January");
  });
});
