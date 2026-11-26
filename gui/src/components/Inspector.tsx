// The side panel, half the window beside the chat. At the top, its title: the chat's, then
// "Working" while the chat's job runs, then "Delivered" (or how it ended) and when. Below it the
// status area: before a job, the chat's own facts (who you're chatting with, the Lead's model,
// usage); once the chat has started a job, that job's status, crew, approvals, folder, models,
// usage and tools. Below that, while the job runs, the background work of every job, newest
// first, with the switch for showing it in the chat too; once the job is delivered, its
// handover and deliverables in place of it.

import { useEffect, useState } from "react";
import { PanelRight, Settings as Gear } from "lucide-react";
import type { JobRow, JobStatus, Report } from "../api/types";
import { usageText } from "../store/conversation";
import { modelLabel } from "../store/models";
import { chatJobIds, useStore } from "../store/store";
import { Deliverables, HandoverText } from "./Handover";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const hm = (d: Date) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;

/** When a background line came in, as "10:28, 19 Nov" (local time). */
export function stamp(at: number): string {
  const d = new Date(at);
  return `${hm(d)}, ${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

/** When a job ended, as the panel's title has it: "09:14 on 26 Nov 2026". */
export function endedAt(iso: string | null | undefined): string {
  const d = new Date(iso ?? "");
  if (!iso || Number.isNaN(d.getTime())) return "";
  return `${hm(d)} on ${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

/** How a job that has ended ended: the title's word and the status area's. */
export function ending(status: string | null): { title: string; status: string } {
  switch (status) {
    case "completed":
      return { title: "Delivered", status: "Handover" };
    case "stopped_by_limit":
      return { title: "Stopped", status: "Stopped at its limit" };
    case "stopped":
      return { title: "Stopped", status: "Stopped, as you asked" };
    case "insufficient_brief":
      return { title: "Ended", status: "Needs more from you" };
    default:
      return { title: "Ended", status: "Didn't finish" };
  }
}

/** The job the panel shows: the chat's running one, else the one picked from the chat, else the latest. */
export function panelJob(jobs: JobRow[], ids: string[], picked: string | null): JobRow | undefined {
  const mine = jobs.filter((j) => ids.includes(j.job_id));
  const latest = (xs: JobRow[]) =>
    xs.slice().sort((a, b) => (a.created_at ?? a.job_id).localeCompare(b.created_at ?? b.job_id)).at(-1);
  return latest(mine.filter((j) => j.active)) ?? mine.find((j) => j.job_id === picked) ?? latest(mine);
}

export function Inspector() {
  const s = useStore();
  const conversation = s.current ? s.conversations[s.current] : null;
  const chatTitle = (s.current && s.titles[s.current]) || (s.screen === "chat" ? "New chat" : "");
  const job = s.screen === "chat" ? panelJob(s.jobs, chatJobIds(s), s.panelJob) : undefined;
  const running = !!job && (job.active || job.status === "running");
  const status = useJobStatus(job);
  const report = useReport(job && !running ? job : undefined);
  const crews = s.summary?.crews ?? [];
  const jobCrews = job ? crews.filter((c) => job.crews.includes(c.name)) : [];
  const models = Array.from(
    new Set(jobCrews.flatMap((c) => c.roles).map((r) => r.model).filter((m): m is string => !!m).map((m) => modelLabel(m, s.models))),
  );
  const tools = status?.tool_names ?? Array.from(new Set(jobCrews.flatMap((c) => c.roles.flatMap((r) => r.tools ?? [])))).sort();
  const waiting = job ? s.approvals.filter((a) => a.job_id === job.job_id).length || job.waiting : 0;
  const you = s.prefs?.name || s.summary?.name;
  const maxTokens = Number(status?.limits?.max_tokens) || 0;
  const usage = job
    ? maxTokens
      ? `${Math.max(job.total_tokens > 0 ? 1 : 0, Math.round((job.total_tokens / maxTokens) * 100))}%`
      : `${job.total_tokens.toLocaleString()} tokens`
    : usageText(conversation?.usage ?? null).replace(/^Usage: /, "") || "0%";
  const ended = job && !running ? ending(job.status) : null;
  const title = !job ? chatTitle : running ? "Working" : `${ended!.title} ${endedAt(job.finished_at)}`.trim();
  const toCrews = () => s.showSettings("crews");

  const before = conversation?.mode === "Brief"
    ? `Writing a brief${you ? ` with ${you}` : ""}`
    : conversation?.kind === "role.add"
      ? "Adding a crew member"
      : `Chatting${you ? ` with ${you}` : ""}`;

  return (
    <aside className="inspector-wrap" aria-label="Side panel">
      <div className="inspector-top" data-tauri-drag-region>
        <div className="title" data-tauri-drag-region>
          {s.screen === "chat" ? title : ""}
        </div>
        <button className="icon-button" aria-label="Hide the side panel" onClick={s.toggleInspector}>
          <PanelRight size={22} />
        </button>
      </div>
      <div className="inspector">
        {s.screen === "chat" && (
          <section className="status-area" aria-label="Status">
            <p>
              <Fact label="Status">{job ? (running ? "Job in progress" : ended!.status) : before}</Fact>
              <Fact label="Crew">{jobCrews.length ? jobCrews.map((c) => c.title).join(", ") : "Not assigned"}</Fact>
              {job && (
                <Fact label="Approvals required">
                  {waiting ? (
                    <button className="link-button" onClick={() => s.show("approvals")}>
                      {waiting}
                    </button>
                  ) : (
                    0
                  )}
                </Fact>
              )}
            </p>
            <p>
              {job && (
                <Fact label="Working folder">
                  <button
                    className="link-button plain"
                    title={s.remote ? "Copy the job's files from the server into Downloads › Ombro" : "Open the job's folder"}
                    onClick={() => void s.openFolder(`${job.path}/workspace`)}
                  >
                    Ombro's files
                  </button>
                </Fact>
              )}
              <Fact label="Model" gear={job ? toCrews : undefined}>
                {job ? models.join(", ") || modelLabel(s.summary?.model, s.models) || "None" : modelLabel(s.summary?.model, s.models) || "None"}
              </Fact>
              <Fact label="Usage">{usage}</Fact>
              {job && (
                <Fact label="Tools" gear={toCrews}>
                  <span title={tools.join(", ")}>{tools.length ? tools.length : "None"}</span>
                </Fact>
              )}
            </p>
          </section>
        )}
        <div className="inspector-body">
          {job && !running ? (
            report ? (
              <>
                <HandoverText report={report} title={chatTitle || job.name} />
                <p className="job-details-link">
                  <button className="link-button" onClick={() => void s.readBrief(job.job_id)}>
                    Job details
                  </button>
                </p>
                <Deliverables report={report} />
              </>
            ) : (
              <p className="muted pad">Opening the handover…</p>
            )
          ) : (
            <Feed />
          )}
        </div>
      </div>
    </aside>
  );
}

/** "Label: value." in the status area, with a gear to change it where there is one. */
function Fact({ label, gear, children }: { label: string; gear?: () => void; children: React.ReactNode }) {
  return (
    <span className="fact">
      <b>{label}:</b> {children}
      {gear ? (
        <button className="icon-button gear" aria-label={`Change the crew's ${label.toLowerCase()}`} onClick={gear}>
          <Gear size={14} />
        </button>
      ) : (
        "."
      )}
    </span>
  );
}

/** The job's own facts (its token limit, its tools), kept up with its usage. */
function useJobStatus(job: JobRow | undefined): JobStatus | null {
  const api = useStore((s) => s.api);
  const [status, setStatus] = useState<JobStatus | null>(null);
  const id = job?.job_id;
  const tokens = job?.total_tokens;
  useEffect(() => {
    if (!api || !id) return setStatus(null);
    let live = true;
    api.get<JobStatus>(`/jobs/${encodeURIComponent(id)}`).then((r) => live && setStatus(r), () => undefined);
    return () => {
      live = false;
    };
  }, [api, id, tokens]);
  return status?.job_id === id ? status : null;
}

/** A delivered job's handover, fetched once it has ended (and seen, once the window is). */
function useReport(job: JobRow | undefined): Report | null {
  const api = useStore((s) => s.api);
  const [report, setReport] = useState<Report | null>(null);
  const id = job?.job_id;
  const finished = job?.finished_at;
  useEffect(() => {
    if (!api || !id) return setReport(null);
    let live = true;
    api.get<Report>(`/jobs/${encodeURIComponent(id)}/report`).then(
      (r) => {
        if (!live) return;
        setReport(r);
        if (!r.seen_at) void api.post(`/jobs/${encodeURIComponent(id)}/seen`).catch(() => undefined);
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [api, id, finished]);
  return report?.job_id === id ? report : null;
}

function Feed() {
  const { feed, prefs, setPref } = useStore();
  const on = prefs?.background_feed !== false;
  return (
    <section className="feed" aria-label="Background work">
      <label className="switch feed-switch">
        <span>Updates in main chat are {on ? "On" : "Off"}</span>
        <input type="checkbox" role="switch" checked={on} onChange={(e) => void setPref("background_feed", e.target.checked)} />
      </label>
      <div className="feed-lines" aria-live="polite">
        {feed.length === 0 && <p className="muted">Nothing running just now.</p>}
        {feed
          .slice()
          .reverse()
          .map((f) => (
            <p key={f.key} className={f.status === "running" ? "" : "ended-line"}>
              <span className="feed-when">{stamp(f.at)} - </span>
              {f.status === "running" ? f.line : `${f.name}: ${f.line}`}
            </p>
          ))}
      </div>
    </section>
  );
}
