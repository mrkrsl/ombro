// The side panel follows the chat on screen, never a job from somewhere else: chatting, then
// working (the background work, newest first), then delivered (the handover and deliverables).
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import type { JobRow, Report } from "../api/types";
import { newConversation } from "../store/conversation";
import { useStore } from "../store/store";
import { endedAt, Inspector, panelJob } from "./Inspector";
import { fileKind, handoverSections } from "./Handover";

const job: JobRow = { active: true, brief: "Invented", chat: null, created_at: null, crews: ["default"], finished_at: null,
  job_id: "invented-teapots-20261117-1200", limit_hit: null, live: false, name: "Invented teapots",
  path: "/invented/jobs/invented-teapots-20261117-1200", seen_at: null, source: "spec", started_at: null,
  status: "running", total_tokens: 10, waiting: 0, cleared_at: null };

const chat = { name: "teapots-20261117-1200", title: "Teapots", path: "", updated_at: "", when: "", id: "c1",
  jobs: [job.job_id], pinned: false };

const HANDOVER = [
  "# Invented teapots", "", "**Completed.** 2026-11-17", "", "## Summary", "", "Two teapot lists are ready.", "",
  "## Deliverables", "", "- teapots.md: /x/teapots.md", "", "## What was done", "", "The researcher counted teapots.", "",
  "---", "", "# Job details", "", "What follows is Ombro's own record.",
].join("\n");

const report = (j: JobRow): Report => ({
  ...j, deliverables: [
    { name: "teapots.md", path: "/x/teapots.md", url: "file:///x/teapots.md", exists: true, kind: "deliverable", role: "r",
      text: "# Teapots\n\nThere are three." },
    { name: "teapots.csv", path: "/x/teapots.csv", url: "file:///x/teapots.csv", exists: true, kind: "deliverable", role: "r" },
  ], report: HANDOVER, report_path: "/x/report.md", workspace: "/x",
});

const status = () => document.querySelector(".status-area")?.textContent ?? "";

describe("the side panel", () => {
  beforeEach(() =>
    useStore.setState({ api: null, screen: "chat", jobs: [job], chats: [], summary: null, approvals: [], schedules: [],
      feed: [], titles: {}, chatOn: null, current: "s1", panelJob: null, prefs: { name: "Ada" } as never,
      conversations: { s1: newConversation("s1", "chat") } }),
  );

  it("doesn't show a running job from no chat on a new chat", () => {
    render(<Inspector />);
    expect(screen.queryByText("Working")).toBeNull();
    expect(status()).toContain("Status: Chatting with Ada.");
  });

  it("shows the job a chat started, with the newest work first", () => {
    useStore.setState({ chatOn: chat.name, chats: [chat], feed: [
      { key: 1, jobId: job.job_id, name: job.name, line: "first step", status: "running", at: 0 },
      { key: 2, jobId: job.job_id, name: job.name, line: "second step", status: "running", at: 0 },
    ] });
    render(<Inspector />);
    expect(screen.getByText("Working")).toBeTruthy();
    expect(status()).toContain("Status: Job in progress.");
    const lines = screen.getAllByText(/step$/).map((p) => p.textContent);
    expect(lines[0]).toContain("second step");
  });

  it("shows the handover and deliverables once the job is delivered", async () => {
    const done = { ...job, active: false, status: "completed", finished_at: "2026-11-26T09:14:00" };
    const api = { get: async (path: string) => (path.endsWith("/report") ? report(done) : { ...done, limits: {} }), post: async () => ({}) };
    useStore.setState({ api: api as never, jobs: [done], chatOn: chat.name, chats: [chat], titles: { s1: "Teapots" } });
    render(<Inspector />);
    expect(screen.getByText(`Delivered ${endedAt(done.finished_at)}`)).toBeTruthy();
    expect(status()).toContain("Status: Handover.");
    await waitFor(() => expect(screen.getByText("Two teapot lists are ready.")).toBeTruthy());
    expect(screen.getByText("What was done")).toBeTruthy();
    expect(screen.getByText("There are three.")).toBeTruthy();
    expect(screen.getByText("CSV file")).toBeTruthy();
    expect(screen.queryByText(/Updates in main chat/)).toBeNull();
  });
});

describe("the handover's parts", () => {
  it("keeps the Lead's sections only, in order", () => {
    expect(handoverSections(HANDOVER).map((s) => s.title)).toEqual(["Summary", "What was done"]);
  });

  it("names a file's kind", () => {
    expect(fileKind("/x/a.csv")).toBe("CSV file");
    expect(fileKind("/x/a.weird")).toBe("WEIRD file");
  });

  it("follows the chat's running job before one picked from the chat", () => {
    const old = { ...job, job_id: "a", active: false, status: "completed", created_at: "2026-11-01" };
    const now = { ...job, job_id: "b", created_at: "2026-11-02" };
    expect(panelJob([old, now], ["a", "b"], "a")?.job_id).toBe("b");
    expect(panelJob([old, { ...now, active: false, status: "completed" }], ["a", "b"], "a")?.job_id).toBe("a");
    expect(panelJob([old, now], ["a"], null)?.job_id).toBe("a");
  });
});
