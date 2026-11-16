// The conversation's lines: the Lead's words, yours, Ombro's notes, progress and job cards.

import { useEffect, useRef, useState } from "react";
import type { Report } from "../api/types";
import type { Line } from "../store/conversation";
import type { Screen } from "../store/store";
import { useStore } from "../store/store";
import { openLink } from "../platform";
import { Markdown } from "./Markdown";
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
  const readReport = useStore((s) => s.readReport);
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
      return <LiveReport jobId={line.jobId} />;
    case "page":
      return (
        <div className="line note done">
          {line.text}{" "}
          <button className="link-button" onClick={() => show(line.screen as Screen, line.args)}>
            Open {line.screen[0].toUpperCase() + line.screen.slice(1)}
          </button>
        </div>
      );
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
              <pre>{f.text.trimEnd()}</pre>
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
    case "ended": {
      const e = line.event;
      const files = e.files ?? [];
      return (
        <div className="line card ended">
          <div className="outcome">{e.text}</div>
          {e.brief && <div className="brief">{e.brief}</div>}
          {files.length > 0 && (
            <div>
              Here's what it made:
              <ul>
                {files.map((f) => (
                  <li key={f.path}>
                    <a
                      href={f.url}
                      onClick={(ev) => {
                        ev.preventDefault();
                        void openLink(f.url);
                      }}
                    >
                      {f.name}
                    </a>
                    {!f.exists && " (no longer there)"}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {(e.waiting ?? 0) > 0 && (
            <p>
              {e.waiting} {e.waiting === 1 ? "action is" : "actions are"} waiting for approval.{" "}
              <button className="link-button" onClick={() => show("approvals")}>
                Approvals
              </button>
            </p>
          )}
          <button className="link-button" onClick={() => void readReport(e.job_id, true)}>
            Read the report
          </button>
        </div>
      );
    }
  }
}

/** A live job's report, fetched when its end arrives, so it sits in the chat where the job ended. */
function LiveReport({ jobId }: { jobId: string }) {
  const api = useStore((s) => s.api);
  const [report, setReport] = useState<Report | null>(null);
  const [problem, setProblem] = useState("");
  useEffect(() => {
    if (!api) return;
    api
      .get<Report>(`/jobs/${encodeURIComponent(jobId)}/report`)
      .then(setReport)
      .catch((e: unknown) => setProblem(e instanceof Error ? e.message : String(e)));
  }, [api, jobId]);
  // It arrives after the chat has scrolled to its end: bring its top into view, to read from there.
  const top = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (report) top.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, [report]);
  if (problem) return <div className="line error">The report could not be opened: {problem}</div>;
  return report ? (
    <div ref={top}>
      <ReportCard report={report} />
    </div>
  ) : (
    <div className="line progress">Opening the report…</div>
  );
}
