// A job's report, then what it made. The report card: where it is, a copy button, the part you
// read as Markdown, what waits for approval, and Ombro's record of how it went, shown on request.
// In the chat (`brief`), only the Lead's short summary shows at first; "Read the full report"
// opens the rest. Under the card, outside it: what the job made and where each is kept, then each
// Markdown file it made in a panel of its own, in full, to read here without leaving the app.

import { useState, type MouseEvent } from "react";
import { Copy, ExternalLink, FileText, FolderOpen } from "lucide-react";
import type { Deliverable, Report } from "../api/types";
import { reportSummary, splitReport, withoutSection } from "../store/conversation";
import { useStore } from "../store/store";
import { Markdown, MarkdownFile } from "./Markdown";

function fileUrl(path: string): string {
  return "file://" + path.split("/").map(encodeURIComponent).join("/");
}

export function ReportCard({ report, brief = false }: { report: Report; brief?: boolean }) {
  const [record, setRecord] = useState(false);
  const [full, setFull] = useState(false);
  const [copied, setCopied] = useState(false);
  const show = useStore((s) => s.show);
  const { openHref, openFolder, remote } = useStore();
  if (!report.report) {
    return (
      <div className="report-card">
        <div className="report-body">Job {report.name} has no report yet (status {report.status}).</div>
      </div>
    );
  }
  const { head, record: rec } = splitReport(report.report);
  const made = report.deliverables.filter((d) => d.kind !== "other");
  const summary = brief ? reportSummary(head) : "";
  const short = !!summary && !full; // the chat's view: the summary alone, until you ask for the rest
  const shown = made.filter((d) => d.text !== undefined && d.exists);
  const opener = (d: Deliverable) => (e: MouseEvent) => {
    e.preventDefault();
    void openHref(d.url);
  };
  return (
    <div className="report-stack">
    <div className="report-card">
      <div className="report-head">
        <span>
          <b>Report:</b>{" "}
          <a
            href={fileUrl(report.report_path)}
            onClick={(e) => {
              e.preventDefault();
              void openHref(fileUrl(report.report_path));
            }}
          >
            {report.name}
          </a>
        </span>
        <span className="report-tools">
          <button
            className="text-button"
            onClick={() => void openFolder(report.workspace)}
            title={remote ? "Copy the job's files from the server into Downloads › Ombro" : "Open the job's folder"}
          >
            <FolderOpen size={16} /> {remote ? "Save to this computer" : "Files"}
          </button>
          <button
            className="text-button"
            onClick={async () => {
              await navigator.clipboard?.writeText(head);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}
          >
            <Copy size={16} /> {copied ? "Copied" : "Copy"}
          </button>
        </span>
      </div>
      <div className="report-body">
        <Markdown text={short ? summary : made.length > 0 ? withoutSection(head, "Deliverables") : head} />
        {report.waiting > 0 && (
          <p>
            {report.waiting} {report.waiting === 1 ? "action is" : "actions are"} waiting for approval.{" "}
            <button className="link-button" onClick={() => show("approvals")}>
              Approvals
            </button>
          </p>
        )}
        {summary && (
          <p>
            <button className="link-button" onClick={() => setFull((f) => !f)} aria-expanded={full}>
              {full ? "Show the summary only" : "Read the full report"}
            </button>
          </p>
        )}
        {rec && !short && (
          <p>
            <button className="link-button" onClick={() => setRecord((r) => !r)}>
              {record ? "Hide Ombro's record" : "Show Ombro's record"}
            </button>
          </p>
        )}
        {record && !short && <Markdown className="record" text={rec} />}
      </div>
    </div>
    {made.length > 0 && (
      <div className="made-files">
        <h4>What the job made</h4>
        <ul>
          {made.map((d) => (
            <li key={d.path}>
              <a href={d.url} onClick={opener(d)}>
                {d.name}
              </a>
              <span className="muted small"> · {where(d, remote)}</span>
              {!d.exists && " (no longer there)"}
            </li>
          ))}
        </ul>
      </div>
    )}
    {shown.map((d) => (
      <section className="deliverable-card" key={d.path} aria-label={d.name}>
        <div className="report-head">
          <span>
            <FileText size={16} /> <b>{d.name}</b> <span className="muted small">· {where(d, remote)}</span>
          </span>
          <a className="text-button" href={d.url} onClick={opener(d)}>
            <ExternalLink size={16} /> Open
          </a>
        </div>
        <div className="report-body">
          <MarkdownFile text={d.text ?? ""} />
        </div>
      </section>
    ))}
    </div>
  );
}

/** Where a file the job made is kept, to come back to: the job's own folder, or a shared folder. */
function where(d: Deliverable, remote: boolean): string {
  const place = d.kind === "deliverable" ? "in the job's folder in Ombro's files" : `${d.kind} in a shared folder`;
  return (remote ? "on the server, " : "") + place;
}
