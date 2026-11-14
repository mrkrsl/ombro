// Settings › Skills (design 16): each skill and the crews whose members use it. Edit and New write
// the skill's description and instructions; Delete moves it aside (C4). Proposals the crews make
// wait under Approvals.

import { useState } from "react";
import type { Crews } from "../../api/types";
import { useStore } from "../../store/store";
import { Field, Problem, RemovePanel, tidy, useAction, useRemoval, useView, type Check } from "./common";

export type SkillRow = { name: string; description: string; source: string; path: string; used_by: string[]; warning: string | null };
export type Library = { skills: SkillRow[]; all_skills: string[]; waiting: { ref: string; name: string | null }[] };
type SkillText = { name: string; description: string; body: string };

/** Who uses a skill, by crew title: members that name it, and members that may use every skill. */
export function usedBy(row: SkillRow, lib: Library, crews: Crews | null): string {
  const title = (who: string) => {
    const crew = who.split("/")[0];
    return crews?.crews.find((c) => c.name === crew)?.title ?? tidy(crew);
  };
  const named = [...new Set(row.used_by.map(title))];
  const every = [...new Set(lib.all_skills.map(title))].filter((t) => !named.includes(t));
  const parts = [...named, ...every];
  return parts.length ? parts.join(", ") : "Not used yet";
}

function SkillForm({ current, onDone, onCancel }: { current: string | null; onDone: (l: Library) => void; onCancel: () => void }) {
  const { run, problem, busy } = useAction();
  const loaded = useView<SkillText>(current ? `/skills/${current}` : "/skills");
  const text = current ? loaded.data : null;
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState<string | null>(null);
  const [body, setBody] = useState<string | null>(null);
  if (current && !text) return <Problem text={loaded.problem || "Loading…"} />;
  const d = description ?? text?.description ?? "";
  const b = body ?? text?.body ?? "";
  return (
    <div className="inline-form">
      <h3>{current ? `Edit ${tidy(current)}` : "New skill"}</h3>
      {!current && (
        <Field label="Name" hint="For example, Document fact checking.">
          <input className="text-input short" value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
      )}
      <Field label="What it does, and when to use it" hint="Crews see this line and decide from it whether to read the skill.">
        <input className="text-input" value={d} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      <Field label="Instructions" hint="The method, step by step, as you'd explain it to someone new.">
        <textarea className="text-area" rows={14} value={b} onChange={(e) => setBody(e.target.value)} />
      </Field>
      <div className="form-actions">
        <button
          className="primary"
          disabled={busy || !d.trim() || !b.trim() || (!current && !title.trim())}
          onClick={async () => {
            const lib = current
              ? await run((api) => api.put<Library>(`/skills/${current}`, { description: d, body: b }))
              : await run((api) => api.post<Library>("/skills", { title, description: d, body: b }));
            if (lib) onDone(lib);
          }}
        >
          {current ? "Save" : "Add skill"}
        </button>
        <button className="secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
      <Problem text={problem} />
    </div>
  );
}

export function Skills() {
  const lib = useView<Library>("/skills");
  const crews = useView<Crews>("/crews");
  const { run } = useAction();
  const removal = useRemoval();
  const show = useStore((s) => s.show);
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const l = lib.data;
  return (
    <>
      <h1 className="page-title">Skills</h1>
      <p className="lede">
        Skills are written instructions for a task you want your crews to do the same way each time. Write one
        yourself here, or ask the Lead to make one from a job you've run.
      </p>
      <Problem text={lib.problem} />
      {l && l.waiting.length > 0 && (
        <p className="note-line">
          {l.waiting.length === 1 ? "A crew has suggested a skill." : `Crews have suggested ${l.waiting.length} skills.`}{" "}
          <button className="text-button strong" onClick={() => show("approvals")}>
            See Approvals
          </button>
        </p>
      )}
      {l && (
        <div className="rows">
          {l.skills.length === 0 && <p className="muted">No skills yet.</p>}
          {l.skills.map((s) => (
            <div className="row-block" key={s.name}>
              <div className="row settings-row three">
                <span className="row-name" title={s.description}>
                  {tidy(s.name)}
                </span>
                <span className="row-detail">{usedBy(s, l, crews.data)}</span>
                <span className="row-actions">
                  <button className="text-button strong" onClick={() => { setEditing(s.name); setAdding(false); }}>
                    Edit
                  </button>
                  <button
                    className="text-button strong"
                    onClick={() => void removal.ask(s.name, () => useStore.getState().api!.get<Check>(`/skills/${s.name}/check`))}
                  >
                    Delete
                  </button>
                </span>
              </div>
              {editing === s.name && (
                <SkillForm current={s.name} onCancel={() => setEditing(null)} onDone={(x) => { lib.set(x); setEditing(null); }} />
              )}
              {removal.target === s.name && (
                <RemovePanel
                  removal={removal}
                  what={tidy(s.name)}
                  onConfirm={async () => {
                    const x = await run((api) => api.del<Library>(`/skills/${s.name}`));
                    if (x) lib.set(x);
                    return !!x;
                  }}
                />
              )}
            </div>
          ))}
        </div>
      )}
      {adding ? (
        <SkillForm current={null} onCancel={() => setAdding(false)} onDone={(x) => { lib.set(x); setAdding(false); }} />
      ) : (
        <div className="form-actions">
          <button className="primary" onClick={() => { setAdding(true); setEditing(null); }}>
            New skill
          </button>
        </div>
      )}
    </>
  );
}
