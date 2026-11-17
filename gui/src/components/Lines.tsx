// The conversation's lines: the Lead's words, yours, Ombro's notes, progress and job cards.

import { useEffect, useRef, useState } from "react";
import type { JobOffer, Report } from "../api/types";
import type { Line } from "../store/conversation";
import type { Screen } from "../store/store";
import { useStore } from "../store/store";
import { whenVisible } from "../platform";
import { Markdown, MarkdownFile } from "./Markdown";
import { ReportCard } from "./ReportCard";

export function Lines({ lines }: { lines: Line[] }) {
  return (
    <div className="lines" role="log" aria-live="polite">
      {lines.map((l) => (
        <LineView key={l.key} line={l} />
      ))}
    </div>
  );
}

function LineView({ line }: { line: Line }) {
  const show = useStore((s) => s.show);
  const feedOn = useStore((s) => s.prefs?.background_feed !== false);
  switch (line.kind) {
    case "lead":
      return <Markdown className="line lead" text={line.text} />;
    case "you":
      return <div className="line you">{line.text}</div>;
    case "note":
      return (
        <div className={`line note ${line.level}`}>
          {line.text}
          {line.items.length > 0 && (
            <ul>
              {line.items.map((item, i) => (
                <li key={i}>{item}</li>
              ))}
            </ul>
          )}
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
    case "report":
      return <ReportCard report={line.report} />;
    case "livereport":
      return <LiveReport jobId={line.jobId} scroll={line.live} />;
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
              {f.diff && f.diff.length > 0 && <pre className="diff">{f.diff.join("\n")}</pre>}
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

/** A job's report, fetched when its end arrives, so it sits in the chat where the job ended. */
function LiveReport({ jobId, scroll }: { jobId: string; scroll: boolean }) {
  const api = useStore((s) => s.api);
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
  // A live job's arrives after the chat has scrolled to its end: bring its top into view, to read
  // from there. A background job's waits where it is, so as not to pull you from what you're doing.
  const top = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (report && scroll) top.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, [report, scroll]);
  if (problem) return <div className="line error">The report could not be opened: {problem}</div>;
  return report ? (
    <div ref={top}>
      <ReportCard report={report} brief />
    </div>
  ) : (
    <div className="line progress">Opening the report…</div>
  );
}
