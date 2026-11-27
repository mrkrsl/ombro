// Settings › Crews (designs 10, 11): each crew with its members (kind, model). The Lead is not listed. A new
// crew, renaming (the title only) and deleting (moved aside, never erased); a new member is an interview,
// so it opens a chat; Edit opens a member's page: name, kind, model and soul.

import { useState } from "react";
import type { Crews as CrewsView, CrewRow, ModelList, RoleDetail, RoleRow } from "../../api/types";
import { useStore } from "../../store/store";
import { Field, Item, Problem, RemovePanel, useAction, useRemoval, useView, type Check } from "./common";
import { modelChoices, modelLabel } from "../../store/models";
import { ModelOptions } from "../../components/ModelOptions";

const KINDS: Record<string, string> = { specialist: "Specialist", helper: "Helper", lead: "Lead" };

/** "Legal Team" → "legal-team", the crew's folder name. */
export function crewName(title: string): string {
  return title
    .trim()
    .toLowerCase()
    .replace(/\s+crew$/, "")
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^[-_]+|[-_]+$/g, "")
    .slice(0, 63);
}

type Editing = { crew: string; role: string };

type Profile = { title: string; kind: string; model: string; soul: string };

function profileOf(r: RoleDetail): Profile {
  return { title: r.title, kind: r.kind, model: r.model, soul: r.soul };
}

/** The model choice that follows the default: "Use default model (claude-opus-5-5)". */
export function defaultChoiceText(models: ModelList | null): string {
  const def = models?.connections.flatMap((c) => c.models).find((m) => m.default);
  return def ? `Use default model (${def.model})` : "Use default model";
}

/** The member's page: name, kind, model and soul, saved together. Save and Cancel wake up on any
 * change; a new name renames them (their folder too, so the reply may come under a new name). */
function MemberEditor({ who, crewTitle, onBack, onRenamed }: {
  who: Editing;
  crewTitle: string;
  onBack: () => void;
  onRenamed: (role: string) => void;
}) {
  const path = `/roles/${encodeURIComponent(who.crew)}/${encodeURIComponent(who.role)}`;
  const role = useView<RoleDetail>(path);
  const models = useView<ModelList>("/models");
  const { run, problem, busy } = useAction();
  const [draft, setDraft] = useState<Partial<Profile>>({});
  const [saved, setSaved] = useState(false);
  const r = role.data;
  if (!r) return <Problem text={role.problem || "Loading…"} />;
  const was = profileOf(r);
  const now: Profile = { ...was, ...draft };
  const changes = (Object.keys(now) as (keyof Profile)[]).filter((k) => now[k] !== was[k]);
  const dirty = changes.length > 0;
  const edit = (k: keyof Profile, v: string) => {
    setDraft((d) => ({ ...d, [k]: v }));
    setSaved(false);
  };
  const all = modelChoices(models.data);
  return (
    <>
      <button className="text-button back-link" onClick={onBack}>
        ← All crews
      </button>
      <h1 className="page-title">{`${crewTitle} – ${r.title}`}</h1>
      <p className="lede">Edit their soul here. Be as detailed as you can about what they do and how they work.</p>
      <Field label="Name" hint="A name no one else in the crew has. Saved briefs that name them follow the change.">
        <input aria-label="Name" className="text-input short" value={now.title} onChange={(e) => edit("title", e.target.value)} />
      </Field>
      <Field
        label="Kind"
        hint="A specialist has a defined scope and the main model; a helper takes narrow, repetitive briefs, often on a cheaper model."
      >
        <span className="form-row tight">
          <select aria-label="Kind" value={now.kind} onChange={(e) => edit("kind", e.target.value)}>
            <option value="specialist">{KINDS.specialist}</option>
            <option value="helper">{KINDS.helper}</option>
          </select>
        </span>
      </Field>
      {all.length > 1 && (
        <Field label="Model">
          <span className="form-row tight">
            <select aria-label="Model" value={now.model} onChange={(e) => edit("model", e.target.value)}>
              {/* "default" follows the default model when it changes; a model picked below stays. */}
              <option value="default">{defaultChoiceText(models.data)}</option>
              {now.model === "cheap" && <option value="cheap">The helpers' model</option>}
              {now.model !== "default" && now.model !== "cheap" && !all.some((m) => m.name === now.model) && (
                <option value={now.model}>{now.model}</option>
              )}
              <ModelOptions models={models.data} />
            </select>
          </span>
        </Field>
      )}
      <Field label="Soul">
        <textarea className="text-area soul" rows={18} value={now.soul} onChange={(e) => edit("soul", e.target.value)} />
      </Field>
      <div className="form-actions">
        <button
          className="primary"
          disabled={busy || !dirty || !now.title.trim() || !now.soul.trim()}
          onClick={async () => {
            const body = Object.fromEntries(changes.map((k) => [k, now[k]]));
            const next = await run((api) => api.put<RoleDetail>(path, body));
            if (next) {
              role.set(next);
              setDraft({});
              setSaved(true);
              if (next.name !== who.role) onRenamed(next.name);
            }
          }}
        >
          Save
        </button>
        <button
          className="secondary"
          disabled={busy || !dirty}
          onClick={() => {
            setDraft({});
            setSaved(false);
          }}
        >
          Cancel
        </button>
        {saved && <span className="saved">Saved</span>}
      </div>
      <Problem text={problem} />
    </>
  );
}

function Member({ crew, role, models, onEdit, removal, onRemoved }: {
  crew: CrewRow;
  role: RoleRow;
  models: ModelList | null;
  onEdit: () => void;
  removal: ReturnType<typeof useRemoval>;
  onRemoved: (v: CrewsView) => void;
}) {
  const { run } = useAction();
  const key = `${crew.name}/${role.name}`;
  return (
    <div className="row-block">
      <Item
        name={role.title}
        details={[
          role.problem ? <span className="problem-inline">Has a problem</span> : KINDS[role.kind ?? ""] ?? role.kind,
          role.problem ? (
            <span title={role.problem}>{role.problem}</span>
          ) : (
            `Model: ${modelLabel(role.model, models)}${role.model === "default" ? " (the default)" : ""}`
          ),
        ]}
        actions={
          <>
            <button className="text-button strong" onClick={onEdit}>
              Edit
            </button>
            <button
              className="text-button strong"
              onClick={() =>
                void removal.ask(key, () => useStore.getState().api!.get<Check>(`/roles/${crew.name}/${role.name}/check`))
              }
            >
              Delete
            </button>
          </>
        }
      />
      {removal.target === key && (
        <RemovePanel
          removal={removal}
          what={role.title}
          onConfirm={async () => {
            const v = await run((api) => api.del<CrewsView>(`/roles/${crew.name}/${role.name}`));
            if (v) onRemoved(v);
            else removal.setProblem("It couldn't be deleted. Try again in a moment.");
            return !!v;
          }}
        />
      )}
    </div>
  );
}

function Crew({ crew, models, onEdit, onChange }: {
  crew: CrewRow;
  models: ModelList | null;
  onEdit: (e: Editing) => void;
  onChange: (v: CrewsView) => void;
}) {
  const { run, problem } = useAction();
  const [renaming, setRenaming] = useState<string | null>(null);
  const removal = useRemoval();
  const crewRemoval = useRemoval();
  const newMember = useStore((s) => s.newMember);
  return (
    <section className="crew-block">
      <div className="crew-head">
        {renaming === null ? (
          <h2>{crew.title}</h2>
        ) : (
          <span className="form-row tight">
            <input
              aria-label="New title"
              className="text-input short"
              value={renaming}
              autoFocus
              onChange={(e) => setRenaming(e.target.value)}
              onKeyDown={(e) => e.key === "Escape" && setRenaming(null)}
            />
            <button
              className="primary small"
              disabled={!renaming.trim()}
              onClick={async () => {
                const v = await run((api) => api.put<CrewsView>(`/crews/${crew.name}`, { title: renaming }));
                if (v) {
                  onChange(v);
                  setRenaming(null);
                }
              }}
            >
              Save
            </button>
            <button className="text-button strong" onClick={() => setRenaming(null)}>
              Cancel
            </button>
          </span>
        )}
        {renaming === null && (
          <span className="row-actions">
            {crew.default ? (
              <span className="muted small" title="Briefs go to this crew unless you say otherwise">
                Default crew
              </span>
            ) : (
              <button
                className="text-button strong"
                title="Briefs go to the default crew unless you say otherwise"
                onClick={async () => {
                  const v = await run((api) => api.put<CrewsView>(`/crews/${crew.name}/default`, {}));
                  if (v) onChange(v);
                }}
              >
                Make default
              </button>
            )}
            <button className="text-button strong" onClick={() => setRenaming(crew.title)}>
              Rename
            </button>
            <button
              className="text-button strong"
              onClick={() => void crewRemoval.ask(crew.name, () => useStore.getState().api!.get<Check>(`/crews/${crew.name}/check`))}
            >
              Delete crew
            </button>
          </span>
        )}
      </div>
      {crewRemoval.target === crew.name && (
        <RemovePanel
          removal={crewRemoval}
          what={`the ${crew.title} and its ${crew.roles.length === 1 ? "member" : `${crew.roles.length} members`}`}
          onConfirm={async () => {
            const v = await run((api) => api.del<CrewsView>(`/crews/${crew.name}`));
            if (v) onChange(v);
            return !!v;
          }}
        />
      )}
      <div className="rows">
        {crew.roles.length === 0 && <p className="muted">No members yet.</p>}
        {crew.roles.map((r) => (
          <Member
            key={r.name}
            crew={crew}
            role={r}
            models={models}
            removal={removal}
            onEdit={() => onEdit({ crew: crew.name, role: r.name })}
            onRemoved={onChange}
          />
        ))}
      </div>
      <div className="form-actions">
        <button className="secondary small" onClick={() => void newMember(crew.name)}>
          New crew member
        </button>
        <span className="muted small">Opens a chat with the Lead</span>
      </div>
      <Problem text={problem} />
    </section>
  );
}

export function Crews() {
  const crews = useView<CrewsView>("/crews");
  const models = useView<ModelList>("/models");
  const { run, problem, busy } = useAction();
  const [editing, setEditing] = useState<Editing | null>(null);
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState("");
  const v = crews.data;

  if (editing) {
    const crewTitle = v?.crews.find((c) => c.name === editing.crew)?.title ?? "";
    return (
      <MemberEditor
        who={editing}
        crewTitle={crewTitle}
        onBack={() => {
          setEditing(null);
          void crews.reload();
        }}
        onRenamed={(role) => setEditing({ ...editing, role })}
      />
    );
  }
  return (
    <>
      <h1 className="page-title">Your crews</h1>
      <p className="lede">
        Crews are groups of specialists and helpers. Group them by team, or however makes sense to you. The Lead
        briefs them and they work on the job together.
      </p>
      {!adding ? (
        <div className="form-actions top">
          <button className="primary" onClick={() => setAdding(true)}>
            New crew
          </button>
        </div>
      ) : (
        <div className="inline-form">
          <Field label="What's the crew called?" hint="For example, Marketing Team. You add its members next.">
            <input className="text-input short" value={title} autoFocus onChange={(e) => setTitle(e.target.value)} />
          </Field>
          <div className="form-actions">
            <button
              className="primary"
              disabled={busy || !crewName(title)}
              onClick={async () => {
                const next = await run((api) => api.post<CrewsView>("/crews", { name: crewName(title), title: title.trim() }));
                if (next) {
                  crews.set(next);
                  setAdding(false);
                  setTitle("");
                }
              }}
            >
              Add crew
            </button>
            <button className="secondary" onClick={() => setAdding(false)}>
              Cancel
            </button>
          </div>
          <Problem text={problem} />
        </div>
      )}
      <Problem text={crews.problem} />
      {v && (
        <>
          {v.crews.map((c) => (
            <Crew key={c.name} crew={c} models={models.data} onEdit={setEditing} onChange={crews.set} />
          ))}
        </>
      )}
    </>
  );
}
