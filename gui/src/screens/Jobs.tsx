// Jobs (design 07): each job as a two-line item, as in Settings: its readable name, and under it
// its status or when it finished (the brief shows on hover). Every job: View brief. Running: Stop.
// Finished: View report, Files and Clear (an ×). Cleared jobs are only hidden (their folders stay), and
// "Show cleared" lists them again, each with Put back.

import { useState } from "react";
import { X } from "lucide-react";
import type { JobRow } from "../api/types";
import { ConfirmButton } from "../components/ConfirmButton";
import { Item } from "./settings/common";
import { useStore } from "../store/store";

export function when(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function statusText(status: string | null, active: boolean, finished: string | null): string {
  if (active) return "Running";
  const s = (status ?? "").replace(/_/g, " ");
  const label = s ? s.charAt(0).toUpperCase() + s.slice(1) : "Not started";
  return finished ? `${label} ${when(finished)}` : label;
}

/** A job that has ended, so it can be cleared: not running now. */
export function isFinished(j: JobRow): boolean {
  return !j.active && j.status !== "running";
}

export function Jobs() {
  const { jobs, readReport, readBrief, stopJob, clearJobs, restoreJob, openFolder, remote } = useStore();
  const [showCleared, setShowCleared] = useState(false);
  const shown = jobs.filter((j) => !j.cleared_at);
  const cleared = jobs.filter((j) => j.cleared_at);
  const finished = shown.filter(isFinished).length;

  const row = (j: JobRow) => (
    <div className="row-block" key={j.job_id}>
      <Item
        name={
          <>
            {j.name}
            {j.live && <span className="badge live" title="Started from a chat and followed there">live</span>}{" "}
            {!j.seen_at && isFinished(j) && !j.cleared_at && <span className="badge">new</span>}
          </>
        }
        title={j.brief}
        details={[statusText(j.status, j.active, j.finished_at)]}
        actions={
          <>
            <button className="text-button" onClick={() => void readBrief(j.job_id)}>
              View brief
            </button>
            {j.active ? (
              <ConfirmButton label="Stop" ask="Stop it? It writes its report first." onConfirm={() => stopJob(j.job_id)} />
            ) : (
              <>
                <button className="text-button" onClick={() => void readReport(j.job_id, false)}>
                  View report
                </button>
                <button className="text-button" onClick={() => void openFolder(`${j.path}/workspace`)}>
                  {remote ? "Save to this computer" : "Files"}
                </button>
                {j.cleared_at ? (
                  <button className="text-button" onClick={() => void restoreJob(j.job_id)}>
                    Put back
                  </button>
                ) : (
                  isFinished(j) && (
                    <button
                      className="text-button"
                      aria-label="Clear"
                      title="Clear it from this list. Its report and files are kept."
                      onClick={() => void clearJobs(j.job_id)}
                    >
                      <X size={16} />
                    </button>
                  )
                )}
              </>
            )}
          </>
        }
      />
    </div>
  );

  return (
    <div className="page">
      <div className="column wide-page">
        <div className="page-title-row">
          <h1 className="page-title">Jobs</h1>
          {finished > 0 && (
            <ConfirmButton
              label="Clear all finished"
              ask={`Clear ${finished} finished job${finished === 1 ? "" : "s"} from this list? Reports and files are kept.`}
              onConfirm={() => clearJobs()}
            />
          )}
        </div>
        {jobs.length === 0 && <p className="muted">No jobs yet. Brief a task in a chat, and its job shows here.</p>}
        {jobs.length > 0 && shown.length === 0 && <p className="muted">No jobs on the list. Cleared jobs are kept below.</p>}
        <div className="rows">{shown.map(row)}</div>
        {cleared.length > 0 && (
          <>
            <p>
              <button className="text-button" onClick={() => setShowCleared((v) => !v)}>
                {showCleared ? "Hide cleared jobs" : `Show cleared jobs (${cleared.length})`}
              </button>
            </p>
            {showCleared && <div className="rows cleared">{cleared.map(row)}</div>}
          </>
        )}
      </div>
    </div>
  );
}
