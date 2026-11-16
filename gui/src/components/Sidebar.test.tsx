// The sidebar: a chat with a job running shows the dotted ring; the others show none.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ChatRow } from "../api/types";
import { useStore } from "../store/store";
import { Sidebar } from "./Sidebar";

const row = (name: string, jobs: string[]): ChatRow =>
  ({ name, title: name, path: `/x/${name}.md`, updated_at: "", when: "", id: null, jobs });

describe("the sidebar", () => {
  it("marks the chats whose jobs are running", () => {
    useStore.setState({ chats: [row("Invented research", ["job-1"]), row("Invented plan", ["job-0"])], running: ["job-1"] });
    render(<Sidebar />);
    expect(screen.getAllByLabelText("A job from this chat is running")).toHaveLength(1);
    expect(screen.getByRole("button", { name: /Invented research/ })).toContainElement(
      screen.getByLabelText("A job from this chat is running"),
    );
  });
});
