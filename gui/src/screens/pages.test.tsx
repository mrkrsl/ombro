// The pages' own rules: cron from a choice of when, a report's two parts, and how a job's status reads.
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import type { ApprovalRow } from "../api/types";
import { splitReport } from "../store/conversation";
import { useStore } from "../store/store";
import { Approvals } from "./Approvals";
import { statusText } from "./Jobs";
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
});

describe("approvals", () => {
  const row: ApprovalRow = { ref: "ab12-1", crews: ["Default Crew"], long_ref: "x", job_id: "invented-20261114-1200",
    job_name: "Invented", status: "pending", tool: "mcp.fake.post_message", summary: "text: invented",
    role: "poster", reason: "The spec asks.", class: "external", edited: false };

  beforeEach(() => useStore.setState({ approvals: [row, { ...row, ref: "ab12-2", summary: "text: another" }] }));

  it("approves only what is ticked", async () => {
    const seen: string[][] = [];
    useStore.setState({ decide: async (_approve: boolean, refs: string[]) => { seen.push(refs); return []; } });
    render(<Approvals />);
    const approve = screen.getByRole("button", { name: /Approve/ });
    expect(approve).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "Choose text: another" }));
    expect(approve).toHaveTextContent("Approve (1)");
    await userEvent.click(approve);
    expect(seen).toEqual([["ab12-2"]]);
  });
});
