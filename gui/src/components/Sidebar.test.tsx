// The sidebar: a chat with a job running shows the small job avatar; the others show none.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ChatRow } from "../api/types";
import { useStore } from "../store/store";
import { Sidebar } from "./Sidebar";

const row = (name: string, jobs: string[]): ChatRow =>
  ({ name, title: name, path: `/x/${name}.md`, updated_at: "", when: "", id: null, jobs, pinned: false });

describe("the sidebar", () => {
  it("marks the chats whose jobs are running", () => {
    useStore.setState({ chats: [row("Invented research", ["job-1"]), row("Invented plan", ["job-0"])], running: ["job-1"] });
    render(<Sidebar />);
    expect(screen.getAllByLabelText("A job from this chat is running")).toHaveLength(1);
    expect(screen.getByRole("button", { name: /Invented research/ })).toContainElement(
      screen.getByLabelText("A job from this chat is running"),
    );
  });

  it("deletes a chat from its own cross, with no confirmation", () => {
    const deleteChat = vi.fn(async () => undefined);
    useStore.setState({ chats: [row("Invented research", []), row("Invented plan", [])], running: [], deleteChat });
    render(<Sidebar />);
    const crosses = screen.getAllByRole("button", { name: "Delete this chat" });
    expect(crosses).toHaveLength(2);
    fireEvent.click(crosses[1]);
    expect(deleteChat).toHaveBeenCalledWith("Invented plan");
  });

  it("marks a pinned chat, and pins or unpins one from its own button", () => {
    const pinChat = vi.fn(async () => undefined);
    useStore.setState({ chats: [{ ...row("Invented research", []), pinned: true }, row("Invented plan", [])], running: [], pinChat });
    render(<Sidebar />);
    expect(screen.getByRole("button", { name: /Invented research/ })).toContainElement(screen.getByLabelText("Pinned"));
    fireEvent.click(screen.getByRole("button", { name: "Unpin this chat" }));
    expect(pinChat).toHaveBeenCalledWith("Invented research", false);
    fireEvent.click(screen.getByRole("button", { name: "Pin this chat" }));
    expect(pinChat).toHaveBeenCalledWith("Invented plan", true);
  });
});
