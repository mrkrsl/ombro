// A session's conversation, built from serve's messages. Pure functions, so they can be tested
// against recorded messages without a socket.

import type {
  CoreEvent,
  EditFilesMessage,
  FileView,
  JobOffer,
  QuestionMessage,
  Report,
  ServerMessage,
} from "../api/types";

export type Line =
  // past: from a reopened chat's record, so its avatar shows still, never animating.
  | { key: string; kind: "lead"; text: string; items?: string[]; past?: boolean }
  | { key: string; kind: "you"; text: string }
  | { key: string; kind: "note"; text: string; items: string[]; level: string }
  | { key: string; kind: "progress"; text: string }
  | { key: string; kind: "error"; text: string }
  | { key: string; kind: "proposal"; files: FileView[]; warnings: string[] }
  | { key: string; kind: "job"; text: string; jobId: string; live: boolean }
  | { key: string; kind: "livereport"; jobId: string; live: boolean }
  | { key: string; kind: "page"; screen: string; args: string; text: string }
  | { key: string; kind: "again"; text: string; offers: JobOffer[] }
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
  editing: EditFilesMessage | null; // drafts to edit by hand, waiting until you say they're saved
  mode: Mode;
  usage: { total: number; max: number | null } | null;
  local: number; // lines added here, not by serve
};

export function newConversation(id: string, kind = "chat", lines: Line[] = []): Conversation {
  return { id, kind, lines, seq: 0, state: "idle", question: null, editing: null, mode: "Chat", usage: null, local: lines.length };
}

/** A line the window adds itself: what you typed, or the greeting carried over from the home screen. */
export function addLocal(c: Conversation, kind: "you" | "lead", text: string): Conversation {
  const local = c.local + 1;
  return { ...c, local, lines: [...c.lines, { key: `local-${local}`, kind, text }] };
}

/** A job's report, opened in the conversation. */
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

/** A report's head without one of its "## " sections (the card lists the job's files itself). */
export function withoutSection(head: string, title: string): string {
  const out: string[] = [];
  let skipping = false;
  for (const line of head.split("\n")) {
    if (line.startsWith("## ") || line.startsWith("# ")) skipping = line.trim() === `## ${title}`;
    if (!skipping) out.push(line);
  }
  return out.join("\n").replace(/\n{3,}/g, "\n\n").trimEnd();
}

/** A report's Summary: the Lead's short paragraph, what the chat shows when a job ends. "" if it has none. */
export function reportSummary(head: string): string {
  const m = /^## Summary[ \t]*\n([\s\S]*?)(?=^## |^# |(?![\s\S]))/m.exec(head);
  if (!m) return "";
  // Ombro's note that it wrote the report itself stays in the report, not in the chat's summary.
  return m[1].split("\n").filter((l) => !l.startsWith("_This report was written by the harness")).join("\n").trim();
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
      return [e.who === "you" ? { key, kind: "you", text: e.text } : { key, kind: "lead", text: e.text, past: true }];
    case "note":
      if (!e.text && !(e.items ?? []).length) return [];
      // Set-up's own lines are written in the Lead's voice, with what they list beneath (such as
      // every argument of an action it asks you to approve) kept in full.
      if (e.level === "lead") return [{ key, kind: "lead", text: e.text, ...((e.items ?? []).length ? { items: e.items } : {}) }];
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
      return [{ key, kind: "job", text: e.text, jobId: e.job_id, live: !!e.live }];
    case "job_ended": {
      // Every job ends the same way: how it went, then its report, here in the chat.
      const waiting = e.waiting ? ` ${e.waiting} ${e.waiting === 1 ? "action waits" : "actions wait"} for your approval.` : "";
      // One that didn't finish: the Lead offers to restart or resume it, with a button for each way.
      const again: Line[] = e.offers?.length
        ? [{ key: `${key}-again`, kind: "again", text: e.again_text ?? "", offers: e.offers }]
        : [];
      return [
        { key, kind: "note", text: e.text + waiting, items: [], level: e.status === "completed" ? "done" : "info" },
        { key: `${key}-report`, kind: "livereport", jobId: e.job_id, live: !!e.live },
        ...again,
      ];
    }
    case "open_page":
      return [{ key, kind: "page", screen: e.screen, args: e.args, text: e.text }];
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
    case "edit_files":
      return { ...next, editing: m };
    case "question_closed":
      return {
        ...next,
        question: c.question && c.question.id === m.id ? null : c.question,
        editing: c.editing && c.editing.id === m.id ? null : c.editing,
      };
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

/** A choice with too many options for buttons (the providers, an endpoint's models): shown as a
 * list to pick from, above the input. The number can be typed too. */
export function listAnswers(q: QuestionMessage | null): { label: string; value: string }[] {
  if (!q || q.field.kind !== "choice") return [];
  const options = q.field.options ?? [];
  return options.length > 6 ? options.map((o) => ({ label: o.label, value: o.key })) : [];
}

function capitalise(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** What the chat's usage looks like in the inspector. */
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

/** How Ombro works, in the Lead's words: the first chat says it (set-up says it too). */
export const HOW_IT_WORKS =
  "Most of what I do happens while you're away: you brief me, the crew works in the background, and I report " +
  "back. When you'd rather watch, we can do it live instead: the crew works here in the chat, and I'll check " +
  "with you as we go. Tell me what you want done, and I'll ask which you'd like.";

/** The opening line, with how Ombro works on the very first chat. */
export function opening(name: string, first: boolean, now = new Date()): string {
  return first ? `${greeting(name, now)}\n\n${HOW_IT_WORKS}` : greeting(name, now);
}
