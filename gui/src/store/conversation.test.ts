// The conversation, built from messages as serve sends them (recorded from a real session).
import { describe, expect, it } from "vitest";
import type { QuestionMessage, ServerMessage } from "../api/types";
import { addLocal, apply, greeting, HOW_IT_WORKS, newConversation, opening, quickAnswers, usageText } from "./conversation";

const S = "a1b2c3";
const ev = (seq: number, event: object) => ({ type: "event", session: S, seq, event }) as ServerMessage;
const yesNo: QuestionMessage = {
  type: "question", session: S, seq: 4, id: "q1", prompt: "Start it now? (y or n, Enter for yes)",
  field: { name: "start_now", label: "Start it now?", kind: "yesno", default: true, options: [], required: false,
    pattern: null, invalid: "", help: "", multiline: false, other: "", mode: "" },
};

function run(messages: ServerMessage[]) {
  return messages.reduce(apply, newConversation(S));
}

describe("a conversation", () => {
  it("shows the Lead, notes and set-up's own lines in the Lead's voice", () => {
    const c = run([
      ev(1, { type: "lead", text: "Hello, **invented**.", asks: false }),
      ev(2, { type: "note", text: "Spec saved.", items: [], spaced: false, level: "done" }),
      ev(3, { type: "note", text: "Let's get started. What's your name?", items: [], spaced: false, level: "lead" }),
    ]);
    expect(c.lines.map((l) => l.kind)).toEqual(["lead", "note", "lead"]);
    expect(c.seq).toBe(3);
  });

  it("ignores what it has already applied, as after a reconnect", () => {
    const once = run([ev(1, { type: "lead", text: "One.", asks: false })]);
    expect(apply(once, ev(1, { type: "lead", text: "One.", asks: false }))).toBe(once);
    expect(apply(once, { ...ev(2, { type: "lead", text: "Not mine.", asks: false }), session: "other" } as ServerMessage)).toBe(once);
  });

  it("opens and closes a question, offering yes and no", () => {
    const c = run([yesNo]);
    expect(c.question?.id).toBe("q1");
    expect(c.lines.at(-1)).toMatchObject({ kind: "lead", text: "Start it now?" });
    expect(quickAnswers(c.question).map((a) => a.label)).toEqual(["Yes", "No"]);
    const closed = apply(c, { type: "question_closed", session: S, seq: 5, id: "q1" });
    expect(closed.question).toBeNull();
  });

  it("asks a choice with no label of its own by its buttons alone", () => {
    const choice: QuestionMessage = { ...yesNo, field: { ...yesNo.field, kind: "choice", label: "",
      options: [{ key: "a", label: "accept", aliases: [] }, { key: "q", label: "quit", aliases: [] }] } };
    const c = run([choice]);
    expect(c.lines).toEqual([]);
    expect(quickAnswers(c.question)).toEqual([{ label: "accept", value: "a" }, { label: "quit", value: "q" }]);
  });

  it("follows the mode, the usage and a fresh chat", () => {
    let c = run([
      ev(1, { type: "mode", mode: "Brief", text: "── Brief mode: a task interview. ──" }),
      ev(2, { type: "usage", scope: "chat", mode: "Chat", total_tokens: 1500, max_tokens: 150000, tool_calls: 0, final: false }),
    ]);
    expect(c.mode).toBe("Brief");
    expect(c.lines.at(-1)).toMatchObject({ kind: "note", text: "Brief mode: a task interview." });
    expect(usageText(c.usage)).toBe("Usage: 1%");
    c = addLocal(c, "you", "Something I typed");
    c = apply(c, ev(3, { type: "cleared" }));
    expect(c.lines).toEqual([]);
  });

  it("keeps a job's end as a card", () => {
    const c = run([ev(1, { type: "job_ended", job_id: "invented-20261114-1200", name: "Invented", status: "completed",
      text: "Your job has finished.", brief: "An invented brief.", limit_hit: null, files: [], waiting: 1 })]);
    expect(c.lines[0]).toMatchObject({ kind: "ended" });
  });

  it("greets as the terminal does", () => {
    expect(greeting("Ada", new Date(2026, 10, 12, 9))).toBe("Good morning Ada. What would you like to get done today?");
    expect(greeting("", new Date(2026, 10, 12, 19))).toMatch(/^Good evening\. /);
  });
});

describe("live jobs", () => {
  const step = (seq: number, live: boolean) =>
    ev(seq, { type: "job_progress", job_id: "bakeries-20261115-1200", name: "Bakeries", role: "lead",
      action: "asked", note: "Sourdough?", text: "Bakeries · lead asked you: Sourdough?", live });

  it("marks a live job's steps, and ends with its report in the chat", () => {
    const c = run([
      step(1, true),
      ev(2, { type: "job_ended", job_id: "bakeries-20261115-1200", name: "Bakeries", status: "completed",
        text: "Your job has finished.", brief: "", limit_hit: null, files: [], waiting: 1, live: true }),
    ]);
    expect(c.lines[0]).toMatchObject({ kind: "job", live: true });
    expect(c.lines.slice(1).map((l) => l.kind)).toEqual(["note", "livereport"]);
    expect(c.lines[1]).toMatchObject({ text: "Your job has finished. 1 action waits for your approval." });
  });

  it("keeps a background job's end as a card to open", () => {
    const c = run([ev(1, { type: "job_ended", job_id: "x", name: "X", status: "completed", text: "Done.", brief: "",
      limit_hit: null, files: [], waiting: 0 })]);
    expect(c.lines.map((l) => l.kind)).toEqual(["ended"]);
  });

  it("shows a suggested page as a line to follow", () => {
    const c = run([ev(1, { type: "open_page", screen: "scheduled", args: "bakeries", text: "Saved as the brief." })]);
    expect(c.lines[0]).toMatchObject({ kind: "page", screen: "scheduled", args: "bakeries" });
  });

  it("says how Ombro works on the very first chat only", () => {
    const at = new Date(2026, 10, 15, 9);
    expect(opening("Ada", true, at)).toContain(HOW_IT_WORKS);
    expect(opening("Ada", false, at)).toBe(greeting("Ada", at));
  });
});
