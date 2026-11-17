// The side panel follows the chat on screen, never a job from somewhere else.
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import type { JobRow } from "../api/types";
import { newConversation } from "../store/conversation";
import { useStore } from "../store/store";
import { Inspector } from "./Inspector";

const job: JobRow = { active: true, brief: "Invented", chat: null, created_at: null, crews: ["default"], finished_at: null,
  job_id: "invented-teapots-20261117-1200", limit_hit: null, live: false, name: "Invented teapots",
  path: "/invented/jobs/invented-teapots-20261117-1200", seen_at: null, source: "spec", started_at: null,
  status: "running", total_tokens: 10, waiting: 0 };

describe("the inspector", () => {
  beforeEach(() =>
    useStore.setState({ api: null, screen: "chat", jobs: [job], chats: [], summary: null, approvals: [], schedules: [],
      feed: [], titles: {}, chatOn: null, current: "s1", conversations: { s1: newConversation("s1", "role.add") } }),
  );

  it("doesn't show a running job from no chat on a new chat", () => {
    render(<Inspector />);
    expect(screen.queryByText(/Invented teapots/)).toBeNull();
    expect(screen.getByText("Adding a crew member")).toBeTruthy();
  });

  it("shows the job a chat started", () => {
    useStore.setState({ chatOn: "teapots-20261117-1200", chats: [{ name: "teapots-20261117-1200", title: "Teapots",
      path: "", updated_at: "", when: "", id: "c1", jobs: [job.job_id] }] });
    render(<Inspector />);
    expect(screen.getByText(/Invented teapots · running/)).toBeTruthy();
  });
});
