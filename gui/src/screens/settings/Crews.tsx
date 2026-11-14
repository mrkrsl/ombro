// Settings › Crews (designs 10, 11): the Lead, then each crew with its members (kind, model). A new
// crew, renaming (the title only) and deleting (moved aside: C4); a new member is an interview,
// so it opens a chat; Edit opens a member's soul and model.

import { useState } from "react";
import type { Crews as CrewsView, CrewRow, ModelList, RoleDetail, RoleRow } from "../../api/types";
import { useStore } from "../../store/store";
import { Field, Problem, RemovePanel, useAction, useRemoval, useView, type Check } from "./common";

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

function SoulEditor({ who, crewTitle, onBack }: { who: Editing; crewTitle: string; onBack: () => void }) {
  const path = `/roles/${encodeURIComponent(who.crew || "lead")}/${encodeURIComponent(who.role)}`;
  const role = useView<RoleDetail>(path);
  const models = useView<ModelList>("/models");
  const { run, problem, busy } = useAction();
  const [soul, setSoul] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const r = role.data;
  if (!r) return <Problem text={role.problem || "Loading…"} />;
  const all = models.data?.connections.flatMap((c) => c.models) ?? [];
  const lead = who.role === "lead";
  return (
    <>
      <button className="text-button back-link" onClick={onBack}>
        ← All crews
      </button>
      <h1 className="page-title">{lead ? "Ombro Lead" : `${crewTitle} – ${r.title}`}</h1>
      <p className="lede">
        {lead
          ? "Edit the Lead's soul here: how it talks with you, briefs your crews and reports back."
          : "Edit their soul here. Be as detailed as you can about what they do and how they work."}
      </p>
      {all.length > 1 && (
        <Field label="Model">
          <span className="form-row tight">
            <select
              aria-label="Model"
              value={r.model}
              onChange={async (e) => {
                const next = await run((api) => api.put<RoleDetail>(`${path}/model`, { name: e.target.value }));
                if (next) role.set(next);
              }}
            >
              {!all.some((m) => m.name === r.model) && <option value={r.model}>{r.model === "cheap" ? "The helpers' model" : "The default model"}</option>}
              {all.map((m) => (
                <option key={m.name} value={m.name}>
                  {m.model}
                </option>
              ))}
            </select>
          </span>
        </Field>
      )}
      <Field label="Soul">
        <textarea
          className="text-area soul"
          rows={18}
          value={soul ?? r.soul}
          onChange={(e) => {
            setSoul(e.target.value);
            setSaved(false);
          }}
        />
      </Field>
      <div className="form-actions">
        <button
          className="primary"
          disabled={busy || soul === null || soul === r.soul}
          onClick={async () => {
            const next = await run((api) => api.put<RoleDetail>(`${path}/soul`, { text: soul }));
            if (next) {
              role.set(next);
              setSoul(null);
              setSaved(true);
            }
          }}
        >
          Save
        </button>
        <button className="secondary" onClick={onBack}>
          Back
        </button>
        {saved && <span className="saved">Saved</span>}
      </div>
      <Problem text={problem} />
    </>
  );
}

function modelLabel(model: string | undefined, models: ModelList | null): string {
  if (!model) return "";
  const all = models?.connections.flatMap((c) => c.models) ?? [];
  const named = all.find((m) => m.name === model);
  if (named) return named.model;
  const def = all.find((m) => m.default);
  if (model === "default") return def ? def.model : "The default";
  if (model === "cheap") return def ? `${def.model} (helpers)` : "The helpers' model";
  return model;
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
      <div className="row settings-row member">
        <span className="row-name">{role.title}</span>
        <span className="row-detail">{role.problem ? <span className="problem-inline">Has a problem</span> : KINDS[role.kind ?? ""] ?? role.kind}</span>
        <span className="row-detail" title={role.problem ?? undefined}>
          {role.problem ? role.problem : `Model: ${modelLabel(role.model, models)}`}
        </span>
        <span className="row-actions">
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
        </span>
      </div>
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
      {crew.description && <p className="muted crew-about">{crew.description}</p>}
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
      <SoulEditor
        who={editing}
        crewTitle={crewTitle}
        onBack={() => {
          setEditing(null);
          void crews.reload();
        }}
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
          <section className="crew-block">
            <div className="crew-head">
              <h2>Ombro Lead</h2>
            </div>
            <div className="rows">
              <div className="row settings-row member">
                <span className="row-name">{v.lead.title || "Lead"}</span>
                <span className="row-detail">Over every crew</span>
                <span className="row-detail">Model: {modelLabel(v.lead.model ?? "default", models.data)}</span>
                <span className="row-actions">
                  <button className="text-button strong" onClick={() => setEditing({ crew: "", role: "lead" })}>
                    Edit
                  </button>
                </span>
              </div>
            </div>
          </section>
          {v.crews.map((c) => (
            <Crew key={c.name} crew={c} models={models.data} onEdit={setEditing} onChange={crews.set} />
          ))}
        </>
      )}
    </>
  );
}
