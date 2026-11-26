// A job's handover: the Lead's account of the job (its summary, what was done, what wasn't and
// why, and any decisions for you), then the deliverables, each Markdown file open to read in a
// panel of its own and any other file as a card to open. The side panel shows it when a job is
// delivered; Jobs › View handover shows the same.

import { useState, type MouseEvent } from "react";
import { Copy, ExternalLink, FileText, NotebookText } from "lucide-react";
import type { Deliverable, Report } from "../api/types";
import { splitReport } from "../store/conversation";
import { useStore } from "../store/store";
import { Markdown, MarkdownFile } from "./Markdown";

/** The Lead's sections, in the order the handover shows them; the rest of the file is Ombro's. */
const SECTIONS = ["Summary", "What was done", "What was not done and why", "Decisions for you"];

/** Ombro's note under a summary it wrote itself: kept in the file, not shown. */
const HARNESS_NOTE = /^_This (report|handover) was written by the harness.*$/gm;

/** The handover's sections the Lead wrote, by heading, each with its text ("" when it has none). */
export function handoverSections(text: string): { title: string; text: string }[] {
  const { head } = splitReport(text);
  const found = new Map<string, string>();
  let at: string | null = null;
  let body: string[] = [];
  const keep = () => {
    // A rule closing the section (before Ombro's part) isn't the Lead's.
    if (at !== null) found.set(at, body.join("\n").replace(HARNESS_NOTE, "").trim().replace(/(^|\n+)-{3,}$/, "").trim());
  };
  for (const line of head.split("\n")) {
    const m = /^##\s+(.+?)\s*$/.exec(line);
    if (m || line.startsWith("# ")) {
      keep();
      at = m ? m[1] : null;
      body = [];
    } else if (at !== null) body.push(line);
  }
  keep();
  return SECTIONS.filter((t) => found.get(t)).map((t) => ({ title: t, text: found.get(t)! }));
}

/** What a file is, as a person would say it: "CSV file", "PDF document". */
export function fileKind(path: string): string {
  const ext = (/\.([A-Za-z0-9]+)$/.exec(path)?.[1] ?? "").toLowerCase();
  const KINDS: Record<string, string> = {
    csv: "CSV file", tsv: "TSV file", pdf: "PDF document", md: "Markdown file", markdown: "Markdown file",
    txt: "Text file", json: "JSON file", html: "Web page", htm: "Web page", xlsx: "Excel spreadsheet",
    xls: "Excel spreadsheet", docx: "Word document", doc: "Word document", pptx: "PowerPoint deck",
    png: "Image", jpg: "Image", jpeg: "Image", gif: "Image", webp: "Image", svg: "Image", zip: "Zip archive",
    py: "Python file", js: "JavaScript file", ts: "TypeScript file", yaml: "YAML file", yml: "YAML file",
  };
  return KINDS[ext] ?? (ext ? `${ext.toUpperCase()} file` : "File");
}

/** The Lead's part of the handover: its title (the chat's name) with the way to the job's details
 * beside it, then each section it wrote. */
export function HandoverText({ report, title }: { report: Report; title: string }) {
  const readBrief = useStore((s) => s.readBrief);
  const sections = report.report ? handoverSections(report.report) : [];
  return (
    <section className="handover" aria-label="Handover">
      <div className="handover-head">
        <h2 className="handover-title">{title}</h2>
        <button className="text-button" onClick={() => void readBrief(report.job_id)}>
          <NotebookText size={16} /> Job details
        </button>
      </div>
      {!report.report && <p className="muted">This job has no handover yet.</p>}
      {sections.map((s) => (
        <div key={s.title} className="handover-section">
          <h3>{s.title}</h3>
          <Markdown className="voice" text={s.text} />
        </div>
      ))}
    </section>
  );
}

/** The files a job made: a line naming them, then each one, Markdown open to read, others to open.
 * Nothing at all when it made none. */
export function Deliverables({ report }: { report: Report }) {
  const made = report.deliverables.filter((d) => d.kind !== "other");
  if (!made.length) return null;
  return (
    <section className="deliverables" aria-label="Deliverables">
      <p className="deliverables-line">
        <b>Deliverables:</b> {made.map((d) => d.name).join("; ")}
      </p>
      {made.map((d) => (
        <DeliverableCard key={d.path} d={d} />
      ))}
    </section>
  );
}

function DeliverableCard({ d }: { d: Deliverable }) {
  const { openHref } = useStore();
  const [copied, setCopied] = useState(false);
  const readable = d.exists && d.text !== undefined;
  const open = (e: MouseEvent) => {
    e.preventDefault();
    void openHref(d.url);
  };
  return (
    <article className={`deliverable${readable ? " readable" : ""}`} aria-label={d.name}>
      <div className="deliverable-head">
        <FileText size={20} strokeWidth={1.5} aria-hidden />
        <span className="deliverable-name">
          {d.name}
          {!readable && (
            <span className="deliverable-kind">{d.exists ? fileKind(d.path) : `${fileKind(d.path)}, no longer there`}</span>
          )}
        </span>
        {readable && (
          <button
            className="text-button"
            onClick={async () => {
              await navigator.clipboard?.writeText(d.text ?? "");
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}
          >
            <Copy size={16} /> {copied ? "Copied" : "Copy"}
          </button>
        )}
        {d.exists && (
          <a className="text-button" href={d.url} onClick={open}>
            <ExternalLink size={16} /> Open
          </a>
        )}
      </div>
      {readable && (
        <div className="deliverable-body">
          <MarkdownFile text={d.text ?? ""} />
        </div>
      )}
    </article>
  );
}
