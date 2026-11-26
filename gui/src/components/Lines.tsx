// The conversation's lines: the Lead's words, yours, Ombro's notes, progress, and the way to each
// delivered job's handover.

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { ArrowRight } from "lucide-react";
import type { JobOffer, Report } from "../api/types";
import { reportSummary, splitReport, type Line } from "../store/conversation";
import type { Screen } from "../store/store";
import { useStore } from "../store/store";
import { whenVisible } from "../platform";
import { Markdown, MarkdownFile } from "./Markdown";
import { ReplyAvatar } from "./Avatar";

/** The Lead's own words: what gets an avatar beside it. */
const spoken = (l: Line | undefined) => l?.kind === "lead" || l?.kind === "again";

/** The lines of a conversation. `chat` names it (so a reply's avatar knows it has played its intro
 * when you come back); `busy`: the Lead is working, and the thinking avatar is the one moving. */
export function Lines({ lines, chat = "", busy = false }: { lines: Line[]; chat?: string; busy?: boolean }) {
  // One avatar per turn of the Lead's: on the first of its lines in a row.
  const turns = lines.map((l, i) => spoken(l) && !spoken(lines[i - 1]));
  // The newest turn may move, unless it came back from a reopened chat's past.
  let newest = -1;
  for (let i = lines.length - 1; i >= 0; i--) {
    if (turns[i]) {
      const l = lines[i];
      newest = l.kind === "lead" && l.past ? -1 : i;
      break;
    }
  }
  return (
    <div className="lines" role="log" aria-live="polite">
      {lines.map((l, i) =>
        turns[i] ? (
          <div key={l.key} className="turn">
            <ReplyAvatar id={`${chat}:${l.key}`} seed={l.kind === "lead" || l.kind === "again" ? l.text : l.key} moving={i === newest && !busy} quiet={busy} />
            <LineView line={l} />
          </div>
        ) : (
          <LineView key={l.key} line={l} />
        ),
      )}
    </div>
  );
}

function LineView({ line }: { line: Line }) {
  const show = useStore((s) => s.show);
  const feedOn = useStore((s) => s.prefs?.background_feed !== false);
  switch (line.kind) {
    case "lead":
      return line.items?.length ? (
        <div className="line-with-items">
          <Markdown className="line lead" text={line.text} />
          <Items items={line.items} />
        </div>
      ) : (
        <Markdown className="line lead" text={line.text} />
      );
    case "you":
      return <div className="line you">{line.text}</div>;
    case "note":
      return (
        <div className={`line note ${line.level}`}>
          {line.text}
          {line.items.length > 0 && <Items items={line.items} />}
        </div>
      );
    case "progress":
      return <div className="line progress">{line.text}</div>;
    case "job":
      // A live job's steps always show; a background job's, unless "updates in main chat" is off
      // (the inspector still shows them).
      return line.live || feedOn ? (
        <div className={`line job${line.live ? " live" : ""}`}>
          <b>{line.live ? "Live:" : "Background:"}</b> {line.text}
        </div>
      ) : null;
    case "livereport":
      return <Delivered jobId={line.jobId} scroll={line.live} />;
    case "page":
      return (
        <div className="line note done">
          {line.text}{" "}
          <button className="link-button" onClick={() => show(line.screen as Screen, line.args)}>
            Open {line.screen[0].toUpperCase() + line.screen.slice(1)}
          </button>
        </div>
      );
    case "again":
      return <AgainOffer text={line.text} offers={line.offers} />;
    case "error":
      return <div className="line error">{line.text}</div>;
    case "proposal":
      return (
        <div className="line card">
          {line.files.map((f) => (
            <div key={f.path}>
              <h4>
                {f.label} ({f.replaces ? "updates the current file" : "new file"})
              </h4>
              <MarkdownFile text={f.text.trimEnd()} />
              {f.diff && f.diff.length > 0 && <Changes lines={f.diff} />}
            </div>
          ))}
          {line.warnings.map((w, i) => (
            <p key={i} className="line note">
              Note: {w}
            </p>
          ))}
        </div>
      );
  }
}

// An item longer than this shows its start and "Show all", never cut silently. As outward.folded.
const FOLD_CHARS = 600;
const FOLD_LINES = 8;

/** The start of an item too long to show whole at first, or null if it is short enough. */
export function folded(item: string): string | null {
  const lines = item.split("\n");
  if (item.length <= FOLD_CHARS && lines.length <= FOLD_LINES) return null;
  return lines.slice(0, FOLD_LINES).join("\n").slice(0, FOLD_CHARS / 2).trimEnd();
}

/** A list beneath a line, such as every argument of an action you're asked to approve: each in full,
 * a long one folded with a "Show all" that opens it. */
export function Items({ items }: { items: string[] }) {
  return (
    <ul className="line-items">
      {items.map((item, i) => (
        <FoldItem key={i} item={item} />
      ))}
    </ul>
  );
}

function FoldItem({ item }: { item: string }) {
  const [open, setOpen] = useState(false);
  const start = folded(item);
  if (start === null) return <li>{item}</li>;
  return (
    <li>
      {open ? item : `${start} …`}{" "}
      <button className="link-button" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? "Show less" : `Show all (${item.length.toLocaleString("en-GB")} characters)`}
      </button>
    </li>
  );
}

/** What a proposal changes in the current file: apart from the file itself, folded away until asked
 * for, wrapped, with no diff headers. Before, a raw diff under the rendered brief read as part
 * of it, and its long lines widened the page. */
export function Changes({ lines }: { lines: string[] }) {
  return (
    <details className="changes">
      <summary>Changes from the current file</summary>
      <div className="diff">
        {lines
          .filter((l) => !l.startsWith("@@"))
          .map((l, i) => {
            const kind = l.startsWith("+") ? "added" : l.startsWith("-") ? "removed" : l === "⋯" ? "gap" : "same";
            return (
              <div key={i} className={kind}>
                {kind === "added" || kind === "removed" ? l.slice(1) || " " : l.replace(/^ /, "") || " "}
              </div>
            );
          })}
      </div>
    </details>
  );
}

/** The Lead's offer to restart or resume a job that didn't finish: a button sends the command, once. */
function AgainOffer({ text, offers }: { text: string; offers: JobOffer[] }) {
  const send = useStore((s) => s.send);
  const [chosen, setChosen] = useState("");
  return (
    <div className="again">
      <Markdown className="line lead" text={text} />
      <p className="again-buttons">
        {offers.map((o, i) => (
          <button
            key={o.command}
            className={i === 0 ? "primary" : "secondary"}
            disabled={!!chosen}
            onClick={() => {
              setChosen(o.command);
              void send(o.command);
            }}
          >
            {o.label}
          </button>
        ))}
      </p>
    </div>
  );
}

/** A job delivered: the Lead's summary of it and the way to its handover in the side panel. Its
 * report is fetched when its end arrives, so it sits in the chat where the job ended. */
function Delivered({ jobId, scroll }: { jobId: string; scroll: boolean }) {
  const api = useStore((s) => s.api);
  const openHandover = useStore((s) => s.openHandover);
  const [report, setReport] = useState<Report | null>(null);
  const [problem, setProblem] = useState("");
  useEffect(() => {
    if (!api) return;
    api
      .get<Report>(`/jobs/${encodeURIComponent(jobId)}/report`)
      .then((r) => {
        setReport(r);
        // It's been shown, once the window is: until then the menu bar keeps its alert.
        whenVisible(() => void api.post(`/jobs/${encodeURIComponent(jobId)}/seen`).catch(() => undefined));
      })
      .catch((e: unknown) => setProblem(e instanceof Error ? e.message : String(e)));
  }, [api, jobId]);
  // A live job's arrives after the chat has scrolled to its end: bring it into view. A background
  // job's waits where it is, so as not to pull you from what you're doing.
  const top = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (report && scroll) top.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }, [report, scroll]);
  if (problem) return <div className="line error">The handover could not be opened: {problem}</div>;
  if (!report) return <div className="line progress">Opening the handover…</div>;
  const summary = report.report ? reportSummary(splitReport(report.report).head) : "";
  const open = () => openHandover(jobId);
  return (
    <div
      ref={top}
      className="delivered-callout"
      role="button"
      tabIndex={0}
      aria-label={`See the handover for ${report.name}`}
      onClick={open}
      onKeyDown={(e: KeyboardEvent) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
      }}
    >
      <ReplyAvatar id={`delivered:${jobId}`} seed={jobId} moving={false} />
      <Markdown className="line lead" text={`${summary || `${report.name} has ended.`} See the handover.`} />
      <ArrowRight className="arrow" size={22} aria-hidden />
    </div>
  );
}
