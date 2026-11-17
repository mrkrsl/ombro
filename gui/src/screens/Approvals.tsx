// Approvals (design 08, as decided in review): tick one or several, then Approve or Decline. Each
// row says in words what it will do, with its crew and job beneath, and opens to show who asked
// and why, and what exactly it will send, as labelled lines rather than JSON.

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { ApprovalDetail } from "../api/types";
import { approvalTitle, argumentLines, toolAction } from "../store/approvals";
import { MarkdownFile } from "../components/Markdown";
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

  async function act(approve: boolean, which: string[] = chosen) {
    setBusy(true);
    setProblems(await decide(approve, which));
    setPicked((p) => new Set([...p].filter((r) => !which.includes(r))));
    setOpen((o) => Object.fromEntries(Object.entries(o).filter(([r]) => !which.includes(r))));
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
            const title = approvalTitle(a.tool, a.arguments);
            return (
              <div key={a.ref} className="row-block">
                <div className="approval">
                  <input type="checkbox" aria-label={`Choose ${title}`} checked={picked.has(a.ref)} onChange={() => toggle(a.ref)} />
                  <button className="approval-head" onClick={() => void expand(a.ref)} aria-expanded={!!d}>
                    <span className="approval-title">{title}</span>
                    <span className="approval-meta">
                      {[a.crews.join(", "), a.job_name].filter(Boolean).join(" · ")}
                    </span>
                  </button>
                  <button className="disclose" aria-label={d ? "Hide the detail" : "Show the detail"} onClick={() => void expand(a.ref)}>
                    {d ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
                  </button>
                </div>
                {d && (
                  <div className="approval-open">
                    {d === "loading" ? (
                      <p className="muted">Opening…</p>
                    ) : (
                      <>
                        <Detail d={d} />
                        <div className="actions compact">
                          <button className="primary small" disabled={busy} onClick={() => void act(true, [a.ref])}>
                            Approve
                          </button>
                          <button className="secondary small" disabled={busy} onClick={() => void act(false, [a.ref])}>
                            Decline
                          </button>
                        </div>
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

function person(role: string): string {
  const r = role.replace(/[-_]+/g, " ").trim();
  return r ? r.charAt(0).toUpperCase() + r.slice(1) : "A role";
}

function Detail({ d }: { d: ApprovalDetail }) {
  const lines = argumentLines(d.arguments);
  const skill = d.skill as { diff?: string; text?: string } | null;
  const [changes, setChanges] = useState(false);
  return (
    <>
      <p>
        {person(d.role)} asks to {toolAction(d.tool).replace(/^./, (c) => c.toLowerCase())}
        {d.reason ? `: ${d.reason.replace(/\.$/, "")}.` : "."}
      </p>
      <div className="what-it-does">
        <b>Approving it will {toolAction(d.tool).replace(/^./, (c) => c.toLowerCase())}, with:</b>
        {lines.length > 0 ? (
          <dl>
            {lines.map(([k, v]) => (
              <div key={k} style={{ display: "contents" }}>
                <dt>{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="muted">Nothing more to send.</p>
        )}
      </div>
      {skill?.text && (
        <div className="proposed-file">
          <MarkdownFile text={String(skill.text)} />
        </div>
      )}
      {skill?.diff && (
        <>
          <button className="text-button" aria-expanded={changes} onClick={() => setChanges(!changes)}>
            {changes ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
            {changes ? "Hide what changes" : "Show what changes"}
          </button>
          {changes && <pre>{String(skill.diff)}</pre>}
        </>
      )}
      <p className="muted">
        From the job {d.job_name}
        {d.edited ? " · changed since it was staged" : ""}
      </p>
    </>
  );
}
