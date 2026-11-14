// A session's conversation, built from serve's messages. Pure functions, so they can be tested
// against recorded messages without a socket.

import type {
  CoreEvent,
  FileView,
  JobEnded,
  QuestionMessage,
  Report,
  ServerMessage,
} from "../api/types";

export type Line =
  | { key: string; kind: "lead"; text: string }
  | { key: string; kind: "you"; text: string }
  | { key: string; kind: "note"; text: string; items: string[]; level: string }
  | { key: string; kind: "progress"; text: string }
  | { key: string; kind: "error"; text: string }
  | { key: string; kind: "proposal"; files: FileView[]; warnings: string[] }
  | { key: string; kind: "job"; text: string; jobId: string }
  | { key: string; kind: "ended"; event: JobEnded }
  | { key: string; kind: "report"; report: Report };

export type Mode = "Chat" | "Brief";
export type SessionState = "working" | "waiting" | "idle" | "ended";

export type Conversation = {
  id: string;
  kind: string;
  lines: Line[];
  seq: number; // the last sequence number applied
  state: SessionState;
  question: QuestionMessage | null;
  mode: Mode;
  usage: { total: number; max: number | null } | null;
  local: number; // lines added here, not by serve
};

export function newConversation(id: string, kind = "chat", lines: Line[] = []): Conversation {
  return { id, kind, lines, seq: 0, state: "idle", question: null, mode: "Chat", usage: null, local: lines.length };
}

/** A line the window adds itself: what you typed, or the greeting carried over from the home screen. */
export function addLocal(c: Conversation, kind: "you" | "lead", text: string): Conversation {
  const local = c.local + 1;
  return { ...c, local, lines: [...c.lines, { key: `local-${local}`, kind, text }] };
}

/** A job's report, opened in the conversation (design 19). */
export function addReport(c: Conversation, report: Report): Conversation {
  const local = c.local + 1;
  return { ...c, local, lines: [...c.lines, { key: `local-${local}`, kind: "report", report }] };
}

/** A report's two parts: what you read, and Ombro's record of how the job went. */
export function splitReport(text: string): { head: string; record: string } {
  for (const mark of ["\n---\n# Ombro's record", "\n# Ombro's record"]) {
    const i = text.indexOf(mark);
    if (i >= 0) return { head: text.slice(0, i).trimEnd(), record: text.slice(i).replace(/^\n---\n/, "\n").trim() };
  }
  return { head: text.trimEnd(), record: "" };
}

/** Labels that only say "your turn": the question's prompt isn't shown as a line for them. */
const PLAIN_LABELS = new Set(["You", "answer", ""]);

/** Whether a question's label is worth a line of its own in the conversation. */
export function showsPrompt(q: QuestionMessage): boolean {
  return q.field.kind === "choice" || q.field.kind === "yesno" || !PLAIN_LABELS.has(q.field.label);
}

function eventLines(e: CoreEvent, key: string): Line[] {
  switch (e.type) {
    case "lead":
      return [{ key, kind: "lead", text: e.text }];
    case "past":
      return [{ key, kind: e.who === "you" ? "you" : "lead", text: e.text }];
    case "note":
      if (!e.text && !(e.items ?? []).length) return [];
      // Set-up's own lines are written in the Lead's voice.
      if (e.level === "lead") return [{ key, kind: "lead", text: e.text }];
      return [{ key, kind: "note", text: e.text, items: e.items ?? [], level: e.level ?? "info" }];
    case "progress":
      return [{ key, kind: "progress", text: e.text }];
    case "error":
      return [{ key, kind: "error", text: e.text }];
    case "proposal":
      return [{ key, kind: "proposal", files: e.files, warnings: e.warnings ?? [] }];
    case "mode":
      return e.text ? [{ key, kind: "note", text: e.text.replace(/^[─\s]+|[─\s]+$/g, ""), items: [], level: "info" }] : [];
    case "job_started":
      return [{ key, kind: "note", text: e.text, items: [], level: "done" }];
    case "job_progress":
      return [{ key, kind: "job", text: e.text, jobId: e.job_id }];
    case "job_ended":
      return [{ key, kind: "ended", event: e }];
    default:
      return []; // usage and cleared change the state, not the lines
  }
}

/** Apply one message from serve to the conversation it belongs to. Messages already applied
 * (a replay after reconnecting) are ignored by their sequence number. */
export function apply(c: Conversation, m: ServerMessage): Conversation {
  if (!("session" in m) || m.session !== c.id) return c;
  if (m.seq <= c.seq) return c;
  const next: Conversation = { ...c, seq: m.seq };
  const key = `s${m.seq}`;
  switch (m.type) {
    case "event": {
      const e = m.event;
      if (e.type === "cleared") return { ...next, lines: [] };
      if (e.type === "usage") return { ...next, usage: { total: e.total_tokens, max: e.max_tokens ?? null } };
      if (e.type === "mode") next.mode = e.mode === "Brief" ? "Brief" : "Chat";
      return { ...next, lines: [...c.lines, ...eventLines(e, key)] };
    }
    case "question": {
      const text = questionText(m);
      const lines = showsPrompt(m) && text ? [...c.lines, { key, kind: "lead" as const, text }] : c.lines;
      return { ...next, question: m, lines };
    }
    case "question_closed":
      return { ...next, question: c.question && c.question.id === m.id ? null : c.question };
    case "session":
      return { ...next, state: m.state, kind: m.kind || c.kind };
    default:
      return next;
  }
}

/** The question as the Lead asks it. Choices and yes or no show as buttons, so the label alone,
 * or nothing when the buttons already say it all; a choice with many options keeps its list,
 * which buttons would crowd. */
export function questionText(q: QuestionMessage): string {
  const f = q.field;
  if (quickAnswers(q).length) return f.label;
  return f.label || q.prompt;
}

/** The answers offered as buttons above the input: a choice's options, or yes and no. */
export function quickAnswers(q: QuestionMessage | null): { label: string; value: string | boolean }[] {
  if (!q) return [];
  const f = q.field;
  if (f.kind === "yesno") {
    const out: { label: string; value: string | boolean }[] = [
      { label: "Yes", value: true },
      { label: "No", value: false },
    ];
    if (f.other) out.push({ label: capitalise(f.other), value: f.other });
    return out;
  }
  if (f.kind === "choice" && (f.options ?? []).length <= 6) {
    return (f.options ?? []).map((o) => ({ label: o.label, value: o.key }));
  }
  return [];
}

function capitalise(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** What the usage looks like in the footer. */
export function usageText(u: Conversation["usage"]): string {
  if (!u) return "";
  if (!u.max) return `Usage: ${u.total.toLocaleString()} tokens`;
  return `Usage: ${Math.floor((u.total * 100) / u.max)}%`;
}

/** The home screen's greeting, as the terminal says it. */
export function greeting(name: string, now = new Date()): string {
  const h = now.getHours();
  const part = h < 12 ? "morning" : h < 18 ? "afternoon" : "evening";
  return `Good ${part}${name ? " " + name : ""}. What would you like to get done today?`;
}
