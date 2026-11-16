// The input: the slash menu, quick answers, and a hidden field for a secret.
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import type { CommandView, QuestionMessage } from "../api/types";
import { newConversation } from "../store/conversation";
import { useStore } from "../store/store";
import { Composer, matching } from "./Composer";

const commands: CommandView[] = [
  { name: "/brief", help: "brief a task", where: "chat", screen: "" },
  { name: "/approvals", help: "what waits for your approval", where: "screen", screen: "approvals" },
  { name: "/chat", help: "back to Chat", where: "chat", screen: "" },
];

beforeEach(() => useStore.setState({ commands, summary: null, notice: "" }));

describe("the composer", () => {
  it("lists the commands that match what has been typed", async () => {
    expect(matching(commands, "/b").map((c) => c.name)).toEqual(["/brief"]);
    expect(matching(commands, "/brief now")).toEqual([]);
    render(<Composer conversation={null} />);
    await userEvent.type(screen.getByRole("textbox", { name: "Message" }), "/");
    expect(screen.getAllByRole("option")).toHaveLength(3);
    await userEvent.type(screen.getByRole("textbox", { name: "Message" }), "a");
    expect(screen.getByRole("option")).toHaveTextContent("/approvals");
    await userEvent.keyboard("{Tab}");
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveValue("/approvals ");
  });

  it("hides what is typed for a secret, and offers buttons for yes or no", () => {
    const field = { name: "key", label: "Paste your API key", kind: "secret", default: null, options: [], required: true,
      pattern: null, invalid: "", help: "", multiline: false, other: "", mode: "" };
    const q: QuestionMessage = { type: "question", session: "s", seq: 1, id: "q", prompt: "", field };
    const { rerender, container } = render(<Composer conversation={{ ...newConversation("s"), question: q }} />);
    expect(container.querySelector("input[type=password]")).toBeInTheDocument();
    const yn = { ...q, field: { ...field, kind: "yesno", label: "Start it now?" } };
    rerender(<Composer conversation={{ ...newConversation("s"), question: yn }} />);
    expect(screen.getByRole("button", { name: "Yes" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "No" })).toBeInTheDocument();
  });

  it("lists a long choice, such as the providers, to pick from", async () => {
    const options = Array.from({ length: 11 }, (_, i) => ({ key: String(i + 1), label: `Invented provider ${i + 1}`, aliases: [] }));
    const field = { name: "endpoint", label: "First, choose a provider", kind: "choice", default: "1", options,
      required: false, pattern: null, invalid: "", help: "", multiline: false, other: "", mode: "" };
    const q: QuestionMessage = { type: "question", session: "s", seq: 1, id: "q", prompt: "", field };
    render(<Composer conversation={{ ...newConversation("s"), question: q }} />);
    expect(screen.getAllByRole("button", { name: /Invented provider/ })).toHaveLength(11);
  });

  it("offers the slash menu while a question is open", async () => {
    const field = { name: "name", label: "What should I call you?", kind: "text", default: null, options: [],
      required: false, pattern: null, invalid: "", help: "", multiline: false, other: "", mode: "" };
    const q: QuestionMessage = { type: "question", session: "s", seq: 1, id: "q", prompt: "", field };
    render(<Composer conversation={{ ...newConversation("s"), question: q }} />);
    await userEvent.type(screen.getByRole("textbox"), "/a");
    expect(screen.getByRole("option")).toHaveTextContent("/approvals");
  });

  it("waits for drafts edited by hand, with Done to hand them back", async () => {
    const editing = { type: "edit_files" as const, session: "s", seq: 2, id: "e1", paths: ["/tmp/invented/1-lead-soul.md"] };
    render(<Composer conversation={{ ...newConversation("s"), state: "waiting", editing }} />);
    expect(screen.getByRole("button", { name: /Open 1-lead-soul.md/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Done" })).toBeInTheDocument();
    expect(screen.queryByText("Thinking")).toBeNull();
  });

  it("shows Thinking while the session works", () => {
    render(<Composer conversation={{ ...newConversation("s"), state: "working" }} />);
    expect(screen.getByText("Thinking")).toBeInTheDocument();
  });
});
