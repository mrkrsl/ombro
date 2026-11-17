// The pages' own rules: cron from a choice of when, a report's two parts, and how a job's status reads.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import type { ApprovalRow, JobRow } from "../api/types";
import { approvalTitle, argumentLines } from "../store/approvals";
import { splitReport } from "../store/conversation";
import { useStore } from "../store/store";
import { Approvals } from "./Approvals";
import { Jobs, statusText } from "./Jobs";
import { cronFor } from "./Scheduled";

describe("the pages", () => {
  it("turns a choice of when into cron", () => {
    expect(cronFor("weekdays", "06:00", 1, "")).toBe("0 6 * * 1-5");
    expect(cronFor("weekly", "18:30", 4, "")).toBe("30 18 * * 4");
    expect(cronFor("daily", "07:05", 0, "")).toBe("5 7 * * *");
    expect(cronFor("monthly", "09:00", 31, "")).toBe("0 9 28 * *");
    expect(cronFor("cron", "", 0, " */15 * * * * ")).toBe("*/15 * * * *");
  });

  it("splits a report from Ombro's record", () => {
    const { head, record } = splitReport("# Invented\n\nThe part you read.\n\n---\n# Ombro's record\n\nHow it went.");
    expect(head).toBe("# Invented\n\nThe part you read.");
    expect(record).toMatch(/^# Ombro's record/);
    expect(splitReport("Only a report.").record).toBe("");
  });

  it("says how a job stands", () => {
    expect(statusText("running", true, null)).toBe("Running");
    expect(statusText("stopped_by_limit", false, null)).toBe("Stopped by limit");
  });

  it("keeps a page's notice on that page only", () => {
    useStore.setState({ api: null, screen: "jobs", toasts: [] });
    useStore.getState().say("That job made no files.");
    useStore.getState().show("jobs");
    expect(useStore.getState().toasts.map((t) => t.text)).toEqual(["That job made no files."]);
    useStore.getState().show("approvals");
    expect(useStore.getState().toasts).toEqual([]);
  });
});

describe("approvals", () => {
  const row: ApprovalRow = { ref: "ab12-1", crews: ["Default Crew"], long_ref: "invented-20261114-1200-1", job_id: "invented-20261114-1200",
    job_name: "Invented", status: "pending", tool: "mcp.fake.post_message", summary: "text: invented",
    role: "poster", reason: "The spec asks.", class: "external", edited: false,
    arguments: { channel: "news", text: "invented" } };

  beforeEach(() => useStore.setState({ approvals: [row, { ...row, ref: "ab12-2", long_ref: "invented-20261114-1200-2", summary: "text: another",
    arguments: { channel: "news", text: "another" } }] }));

  it("says what an approval will do, in words", () => {
    expect(approvalTitle("mcp.sheets.post_message", { channel: "news", text: "The reservoir is full" }))
      .toBe("Post message in Sheets: “The reservoir is full”");
    expect(approvalTitle("skills.propose", {})).toBe("Add or change a skill");
    expect(argumentLines({ channel: "news", to_address: ["a", "b"] })).toEqual([["Channel", "news"], ["To address", "a, b"]]);
  });

  it("approves only what is ticked", async () => {
    const seen: string[][] = [];
    useStore.setState({ decide: async (_approve: boolean, refs: string[]) => { seen.push(refs); return []; } });
    render(<Approvals />);
    const approve = screen.getByRole("button", { name: /Approve/ });
    expect(approve).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "Choose Post message in Fake: “another”" }));
    expect(approve).toHaveTextContent("Approve (1)");
    await userEvent.click(approve);
    expect(seen).toEqual([["invented-20261114-1200-2"]]);
  });

  it("shows an opened skill as formatted Markdown, with its own Approve and Decline", async () => {
    const seen: [boolean, string[]][] = [];
    const detail = { ...row, ref: "ab12-2", tool: "skills.propose", arguments: { name: "invented" },
      skill: { name: "invented", text: "---\nname: invented\n---\n# Invented skill\n\nDo **this** first." } };
    useStore.setState({
      approvals: [row, { ...row, ref: "ab12-2", long_ref: "invented-20261114-1200-2", tool: "skills.propose",
        arguments: { name: "invented" } }],
      call: (async () => detail) as never,
      decide: async (approve: boolean, refs: string[]) => { seen.push([approve, refs]); return []; },
    });
    render(<Approvals />);
    await userEvent.click(screen.getAllByRole("button", { name: "Show the detail" })[1]);
    expect(await screen.findByRole("heading", { name: "Invented skill" })).toBeInTheDocument();
    expect(screen.getByText("this").tagName).toBe("STRONG");
    // Nothing ticked: the opened one's own buttons work all the same.
    const opened = within(document.querySelector(".approval-open") as HTMLElement);
    expect(opened.getByRole("button", { name: "Approve" })).toBeEnabled();
    await userEvent.click(opened.getByRole("button", { name: "Decline" }));
    expect(seen).toEqual([[false, ["invented-20261114-1200-2"]]]);
  });

  it("tells apart two jobs' approvals with the same short reference", async () => {
    // Two jobs started in the same minute: both actions are `1015-1`.
    const asked: string[] = [];
    const seen: string[][] = [];
    useStore.setState({
      approvals: [{ ...row, ref: "1015-1", long_ref: "gophers-20261117-1015-1" },
        { ...row, ref: "1015-1", long_ref: "reservoir-20261117-1015-1", arguments: { channel: "news", text: "another" } }],
      call: (async (fn: (api: { get: (path: string) => Promise<unknown> }) => Promise<unknown>) =>
        fn({ get: async (path: string) => { asked.push(path); return { ...row, skill: null }; } })) as never,
      decide: async (_approve: boolean, refs: string[]) => { seen.push(refs); return []; },
    });
    render(<Approvals />);
    await userEvent.click(screen.getAllByRole("button", { name: "Show the detail" })[1]);
    expect(asked).toEqual(["/approvals/reservoir-20261117-1015-1"]);
    await userEvent.click(within(document.querySelector(".approval-open") as HTMLElement).getByRole("button", { name: "Approve" }));
    expect(seen).toEqual([["reservoir-20261117-1015-1"]]);
  });

  it("closes an approval that can't be opened, rather than leaving it opening", async () => {
    useStore.setState({ call: (async () => undefined) as never });
    render(<Approvals />);
    await userEvent.click(screen.getAllByRole("button", { name: "Show the detail" })[0]);
    expect(screen.queryByText("Opening…")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Show the detail" })).toHaveLength(2);
  });
});

describe("the Jobs page", () => {
  const job: JobRow = { job_id: "invented-20261117-0900", name: "Invented", crews: ["default"], status: "completed",
    active: false, brief: "An invented brief", source: "chat", created_at: null, started_at: null,
    finished_at: "2026-11-17T09:10:00Z", limit_hit: null, total_tokens: 10, waiting: 0, seen_at: "2026-11-17T09:11:00Z",
    path: "/jobs/invented-20261117-0900", chat: null, live: false, cleared_at: null };
  const running: JobRow = { ...job, job_id: "busy-20261117-0930", name: "Busy", status: "running", active: true,
    finished_at: null, seen_at: null };
  const gone: JobRow = { ...job, job_id: "old-20261116-0800", name: "Old", cleared_at: "2026-11-17T10:00:00Z" };

  it("offers View brief for running jobs too, and Clear only for finished ones", async () => {
    const briefs: string[] = [];
    const cleared: (string | undefined)[] = [];
    useStore.setState({
      jobs: [running, job],
      readBrief: async (id: string) => { briefs.push(id); },
      clearJobs: async (id?: string) => { cleared.push(id); },
    });
    render(<Jobs />);
    const busy = screen.getByText("Busy").closest(".item") as HTMLElement;
    expect(within(busy).queryByRole("button", { name: "Clear" })).toBeNull();
    await userEvent.click(within(busy).getByRole("button", { name: "View brief" }));
    const done = screen.getByText("Invented").closest(".item") as HTMLElement;
    const names = within(done).getAllByRole("button").map((b) => b.textContent);
    expect(names).toEqual(["View brief", "View report", "Files", "Clear"]);
    await userEvent.click(within(done).getByRole("button", { name: "Clear" }));
    expect(briefs).toEqual(["busy-20261117-0930"]);
    expect(cleared).toEqual(["invented-20261117-0900"]);
  });

  it("asks before clearing all finished jobs", async () => {
    const cleared: (string | undefined)[] = [];
    useStore.setState({ jobs: [running, job], clearJobs: async (id?: string) => { cleared.push(id); } });
    render(<Jobs />);
    await userEvent.click(screen.getByRole("button", { name: "Clear all finished" }));
    expect(cleared).toEqual([]);
    expect(screen.getByText(/Clear 1 finished job from this list\?/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Yes" }));
    expect(cleared).toEqual([undefined]);
  });

  it("hides cleared jobs until asked, and can put one back", async () => {
    const back: string[] = [];
    useStore.setState({ jobs: [job, gone], restoreJob: async (id: string) => { back.push(id); } });
    render(<Jobs />);
    expect(screen.queryByText("Old")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Show cleared jobs (1)" }));
    const old = screen.getByText("Old").closest(".item") as HTMLElement;
    await userEvent.click(within(old).getByRole("button", { name: "Put back" }));
    expect(back).toEqual(["old-20261116-0800"]);
  });
});
