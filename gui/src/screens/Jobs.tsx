// Jobs (design 07): each job as a two-line item, as in Settings: its readable name, and under it
// its status or when it finished (the brief shows on hover). Running: Stop. Finished: View report and Files.

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

export function Jobs() {
  const { jobs, readReport, stopJob, openFolder, remote } = useStore();
  return (
    <div className="page">
      <div className="column wide-page">
        <h1 className="page-title">Jobs</h1>
        {jobs.length === 0 && <p className="muted">No jobs yet. Brief a task in a chat, and its job shows here.</p>}
        <div className="rows">
          {jobs.map((j) => (
            <div className="row-block" key={j.job_id}>
              <Item
                name={
                  <>
                    {j.name}
                    {j.live && <span className="badge live" title="Started from a chat and followed there">live</span>}{" "}
                    {!j.seen_at && !j.active && j.status !== "running" && <span className="badge">new</span>}
                  </>
                }
                title={j.brief}
                details={[statusText(j.status, j.active, j.finished_at)]}
                actions={
                  j.active ? (
                    <ConfirmButton label="Stop" ask="Stop it? It writes its report first." onConfirm={() => stopJob(j.job_id)} />
                  ) : (
                    <>
                      <button className="text-button" onClick={() => void readReport(j.job_id, false)}>
                        View report
                      </button>
                      <button className="text-button" onClick={() => void openFolder(`${j.path}/workspace`)}>
                        {remote ? "Save to this computer" : "Files"}
                      </button>
                    </>
                  )
                }
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
