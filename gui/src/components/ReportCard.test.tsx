import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Report } from "../api/types";
import { ReportCard } from "./ReportCard";

const report = {
  name: "Rivers", job_id: "rivers-1", report: "# Rivers\n\nDone.", report_path: "/x/report.md", workspace: "/x/workspace",
  waiting: 2, status: "completed",
  deliverables: [
    { name: "The note", path: "/x/workspace/note.md", url: "file:///x/workspace/note.md", exists: true, kind: "deliverable", role: "writer", text: "## Invented heading" },
    { name: "data.csv", path: "/x/workspace/data.csv", url: "file:///x/workspace/data.csv", exists: true, kind: "deliverable", role: "writer" },
  ],
} as unknown as Report;

describe("ReportCard", () => {
  it("opens a Markdown file it made right here, and only those", () => {
    render(<ReportCard report={report} />);
    expect(screen.getAllByRole("button", { name: "Show here" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Show here" }));
    expect(screen.getByRole("heading", { name: "Invented heading" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Hide" }));
    expect(screen.queryByRole("heading", { name: "Invented heading" })).toBeNull();
  });

  it("says what waits for approval", () => {
    render(<ReportCard report={report} />);
    expect(screen.getByText(/2 actions are waiting for approval/)).toBeInTheDocument();
  });
});
