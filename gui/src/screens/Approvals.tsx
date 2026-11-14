// Approvals (design 08, as decided in review): tick one or several, then Approve or Decline. Each
// row opens to show what it is: the tool, what it will send and where, the role's reason, and
// the job it came from.

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { ApprovalDetail } from "../api/types";
import { useStore } from "../store/store";

export function Approvals() {
  const { approvals, decide, call } = useStore();
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<Record<string, ApprovalDetail | "loading">>({});
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const refs = approvals.map((a) => a.ref);
  const chosen = refs.filter((r) => picked.has(r));

  function toggle(ref: string) {
    setPicked((p) => {
      const next = new Set(p);
      if (next.has(ref)) next.delete(ref);
      else next.add(ref);
      return next;
    });
  }

  async function expand(ref: string) {
    if (open[ref]) {
      setOpen(({ [ref]: _, ...rest }) => rest);
      return;
    }
    setOpen((o) => ({ ...o, [ref]: "loading" }));
    const d = await call((api) => api.get<ApprovalDetail>(`/approvals/${encodeURIComponent(ref)}`));
    if (d) setOpen((o) => ({ ...o, [ref]: d }));
  }

  async function act(approve: boolean) {
    setBusy(true);
    setProblems(await decide(approve, chosen));
    setPicked(new Set());
    setBusy(false);
  }

  return (
    <div className="page">
      <div className="column wide-page">
        <h1 className="page-title">Approvals needed</h1>
        {approvals.length === 0 && <p className="muted">Nothing is waiting for you.</p>}
        {approvals.length > 0 && (
          <label className="check-all">
            <input
              type="checkbox"
              checked={chosen.length === refs.length}
              onChange={(e) => setPicked(new Set(e.target.checked ? refs : []))}
            />
            All
          </label>
        )}
        <div className="rows">
          {approvals.map((a) => {
            const d = open[a.ref];
            return (
              <div key={a.ref} className="row-block">
                <div className="row">
                  <input type="checkbox" aria-label={`Choose ${a.summary}`} checked={picked.has(a.ref)} onChange={() => toggle(a.ref)} />
                  <button className="row-name text-button plain" onClick={() => void expand(a.ref)} aria-expanded={!!d}>
                    {d ? <ChevronDown size={16} /> : <ChevronRight size={16} />} {a.tool}: {a.summary}
                  </button>
                  <span className="row-detail">{a.crews.join(", ")}</span>
                  <span className="row-when">{a.job_name}</span>
                </div>
                {d && (
                  <div className="row-open">
                    {d === "loading" ? (
                      <p className="muted">Opening…</p>
                    ) : (
                      <>
                        <p>
                          <b>Tool:</b> {d.tool} ({d.class || "outward"}) · <b>Status:</b> {d.status}
                        </p>
                        <p>
                          <b>Why:</b> {d.reason || "No reason given."} <span className="muted">({d.role || "a role"})</span>
                        </p>
                        <p>
                          <b>From the job:</b> {d.job_name}
                        </p>
                        <b>What it will send:</b>
                        <pre>{JSON.stringify(d.arguments, null, 2)}</pre>
                        {d.skill && <pre>{String((d.skill as { diff?: string; text?: string }).diff || (d.skill as { text?: string }).text || "")}</pre>}
                      </>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
        {approvals.length > 0 && (
          <div className="actions">
            <button className="primary" disabled={!chosen.length || busy} onClick={() => void act(true)}>
              Approve{chosen.length ? ` (${chosen.length})` : ""}
            </button>
            <button className="secondary" disabled={!chosen.length || busy} onClick={() => void act(false)}>
              Decline{chosen.length ? ` (${chosen.length})` : ""}
            </button>
            <span className="muted">Declined actions never run.</span>
          </div>
        )}
        {problems.map((p) => (
          <p key={p} className="notice">
            {p}
          </p>
        ))}
      </div>
    </div>
  );
}
