// Settings › Skills (design 16): each skill and the crews whose members use it. Edit and New write
// the skill's description and instructions; Delete moves it aside (C4). Add from a folder reads a
// skill's folder on this computer and sends its files to serve, which checks and copies them as
// `ombro skills add` does. Proposals the crews make wait under Approvals.

import { useState } from "react";
import type { Crews } from "../../api/types";
import { MarkdownFile } from "../../components/Markdown";
import { readSkillFolder, type SkillFolder } from "../../platform";
import { useStore } from "../../store/store";
import { Field, Item, Problem, RemovePanel, tidy, useAction, useRemoval, useView, type Check } from "./common";

export type SkillRow = { name: string; description: string; source: string; path: string; used_by: string[]; warning: string | null };
export type Library = { skills: SkillRow[]; all_skills: string[]; waiting: { ref: string; name: string | null }[] };
type SkillText = { name: string; description: string; body: string };
export type ImportView = {
  name: string; replaces: boolean; flags: string[]; references: string[]; skipped: string[]; text: string;
  diff: string | null;
};
type Imported = ImportView & Library & { added: string; kept: string | null; not_given: string[] };

/** A pasted whole SKILL.md: its front matter gives the name and description. */
const pastedWhole = (body: string) => body.trimStart().startsWith("---");

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
      <Field
        label="Instructions"
        hint="The method, step by step, as you'd explain it to someone new. You can paste a whole SKILL.md here, front matter and all."
      >
        <textarea className="text-area" rows={14} value={b} onChange={(e) => setBody(e.target.value)} />
      </Field>
      <div className="form-actions">
        <button
          className="primary"
          disabled={busy || !b.trim() || (!pastedWhole(b) && (!d.trim() || (!current && !title.trim())))}
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

/** Members by name and crew, as Settings shows them: "Legal Researcher (Legal Crew)". */
function memberNames(who: string[], crews: Crews | null): string {
  return who
    .map((w) => {
      const [crew, role] = w.split("/");
      const c = crews?.crews.find((x) => x.name === crew);
      const r = c?.roles.find((x) => x.name === role);
      return `${r?.title ?? tidy(role)} (${c?.title ?? tidy(crew)})`;
    })
    .join(", ");
}

function ImportPanel({ folder, view, crews, onDone, onCancel }: {
  folder: SkillFolder;
  view: ImportView;
  crews: Crews | null;
  onDone: (l: Library) => void;
  onCancel: () => void;
}) {
  const { run, problem, busy } = useAction();
  const [added, setAdded] = useState<Imported | null>(null);
  const [given, setGiven] = useState(false);
  if (added) {
    const missing = added.not_given;
    return (
      <div className="inline-form" role="status">
        <h3>Added {tidy(added.name)}</h3>
        {missing.length === 0 || given ? (
          <p>{given ? "Every crew member uses it now." : "Every crew member uses it: they may all use every skill."}</p>
        ) : (
          <>
            <p>
              Members that may use every skill have it now. {missing.length === 1 ? "This member has" : "These members have"} a
              list of skills, so {missing.length === 1 ? "it doesn't" : "they don't"} get it yet: {memberNames(missing, crews)}.
            </p>
            <div className="form-actions">
              <button
                className="primary"
                disabled={busy}
                onClick={async () => {
                  const ok = await run(async (api) => {
                    for (const who of missing) await api.post(`/skills/${added.name}/give`, { role: who });
                    return api.get<Library>("/skills");
                  });
                  if (ok) {
                    setGiven(true);
                    onDone(ok);
                  }
                }}
              >
                Give it to {missing.length === 1 ? "them" : `all ${missing.length}`}
              </button>
            </div>
          </>
        )}
        <div className="form-actions">
          <button className="secondary" onClick={onCancel}>
            Done
          </button>
        </div>
        <Problem text={problem} />
      </div>
    );
  }
  return (
    <div className="inline-form">
      <h3>
        {tidy(view.name)}: {view.replaces ? "a new version of the skill you have" : "new to your library"}
      </h3>
      <p className="muted small">From {folder.path}</p>
      {view.flags.length > 0 && (
        <p className="problem-inline">
          It has signs of injected text ({view.flags.join(", ")}). Read it closely before you add it.
        </p>
      )}
      <p className="muted small">
        References copied with it: {view.references.length ? view.references.join(", ") : "none"}.
        {view.skipped.length > 0 &&
          ` Not copied (scripts, pictures, links and other files): ${view.skipped.slice(0, 10).join(", ")}${view.skipped.length > 10 ? ` and ${view.skipped.length - 10} more` : ""}.`}
      </p>
      <div className="proposed-file">
        <MarkdownFile text={view.text} />
      </div>
      {view.diff && <pre className="import-diff">{view.diff}</pre>}
      <div className="form-actions">
        <button
          className="primary"
          disabled={busy}
          onClick={async () => {
            const x = await run((api) =>
              api.post<Imported>("/skills/import", { folder: folder.folder, path: folder.path, files: folder.files, skipped: folder.skipped, add: true }),
            );
            if (x) {
              setAdded(x);
              onDone(x);
            }
          }}
        >
          {view.replaces ? "Replace it" : "Add skill"}
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
  const [importing, setImporting] = useState<{ folder: SkillFolder; view: ImportView } | null>(null);
  const picking = useAction();
  const l = lib.data;
  return (
    <>
      <h1 className="page-title">Skills</h1>
      <p className="lede">
        Skills are written instructions for a task you want your crews to do the same way each time. Write one
        yourself here, add one from a folder, or ask the Lead to make one from a job you've run.
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
              <Item
                name={tidy(s.name)}
                title={s.description}
                details={[<span title="The crews whose members use it">{usedBy(s, l, crews.data)}</span>]}
                actions={
                  <>
                    <button className="text-button strong" onClick={() => { setEditing(s.name); setAdding(false); }}>
                      Edit
                    </button>
                    <button
                      className="text-button strong"
                      onClick={() => void removal.ask(s.name, () => useStore.getState().api!.get<Check>(`/skills/${s.name}/check`))}
                    >
                      Delete
                    </button>
                  </>
                }
              />
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
      ) : importing ? (
        <ImportPanel
          folder={importing.folder}
          view={importing.view}
          crews={crews.data}
          onDone={(x) => lib.set({ skills: x.skills, all_skills: x.all_skills, waiting: x.waiting })}
          onCancel={() => setImporting(null)}
        />
      ) : (
        <>
          <div className="form-actions">
            <button className="primary" onClick={() => { setAdding(true); setEditing(null); }}>
              New skill
            </button>
            <button
              className="secondary"
              disabled={picking.busy}
              onClick={async () => {
                setEditing(null);
                const got = await picking.run(async (api) => {
                  const folder = await readSkillFolder();
                  if (!folder) return null;
                  const view = await api.post<ImportView>("/skills/import", {
                    folder: folder.folder, path: folder.path, files: folder.files, skipped: folder.skipped, add: false,
                  });
                  return { folder, view };
                });
                if (got) setImporting(got);
              }}
            >
              Add from a folder
            </button>
          </div>
          <Problem text={picking.problem} />
        </>
      )}
    </>
  );
}
