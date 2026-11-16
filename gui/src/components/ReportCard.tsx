// A job's report (design 19): where it is, a copy button, the part you read as Markdown, what the
// job made (a Markdown file can open here too), what waits for approval, and Ombro's record of
// how it went, shown on request.

import { useState } from "react";
import { Copy, FolderOpen } from "lucide-react";
import type { Report } from "../api/types";
import { splitReport } from "../store/conversation";
import { openLink } from "../platform";
import { useStore } from "../store/store";
import { Markdown, MarkdownFile } from "./Markdown";

function fileUrl(path: string): string {
  return "file://" + path.split("/").map(encodeURIComponent).join("/");
}

export function ReportCard({ report }: { report: Report }) {
  const [record, setRecord] = useState(false);
  const [copied, setCopied] = useState(false);
  const show = useStore((s) => s.show);
  const [open, setOpen] = useState<string[]>([]); // the Markdown files shown here, by path
  const toggle = (path: string) => setOpen((o) => (o.includes(path) ? o.filter((p) => p !== path) : [...o, path]));
  if (!report.report) {
    return (
      <div className="report-card">
        <div className="report-body">Job {report.name} has no report yet (status {report.status}).</div>
      </div>
    );
  }
  const { head, record: rec } = splitReport(report.report);
  const made = report.deliverables.filter((d) => d.kind !== "other");
  return (
    <div className="report-card">
      <div className="report-head">
        <span>
          <b>Report:</b>{" "}
          <a
            href={fileUrl(report.report_path)}
            onClick={(e) => {
              e.preventDefault();
              void openLink(fileUrl(report.report_path));
            }}
          >
            {report.name}
          </a>
        </span>
        <span className="report-tools">
          <button
            className="text-button"
            onClick={() => void openLink(fileUrl(report.workspace))}
            title="Open the job's folder"
          >
            <FolderOpen size={16} /> Files
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
        <Markdown text={head} />
        {made.length > 0 && (
          <>
            <h4>What the job made</h4>
            <ul>
              {made.map((d) => (
                <li key={d.path}>
                  <a
                    href={d.url}
                    onClick={(e) => {
                      e.preventDefault();
                      void openLink(d.url);
                    }}
                  >
                    {d.kind === "deliverable" ? d.name : `${d.name} (${d.kind} in a shared folder)`}
                  </a>
                  {!d.exists && " (no longer there)"}
                  {d.text !== undefined && (
                    <>
                      {" · "}
                      <button className="link-button" onClick={() => toggle(d.path)} aria-expanded={open.includes(d.path)}>
                        {open.includes(d.path) ? "Hide" : "Show here"}
                      </button>
                      {open.includes(d.path) && (
                        <div className="shown-file">
                          <MarkdownFile text={d.text} />
                        </div>
                      )}
                    </>
                  )}
                </li>
              ))}
            </ul>
          </>
        )}
        {report.waiting > 0 && (
          <p>
            {report.waiting} {report.waiting === 1 ? "action is" : "actions are"} waiting for approval.{" "}
            <button className="link-button" onClick={() => show("approvals")}>
              Approvals
            </button>
          </p>
        )}
        {rec && (
          <p>
            <button className="link-button" onClick={() => setRecord((r) => !r)}>
              {record ? "Hide Ombro's record" : "Show Ombro's record"}
            </button>
          </p>
        )}
        {record && <Markdown className="record" text={rec} />}
      </div>
    </div>
  );
}
