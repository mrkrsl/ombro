// The inspector (design 06). Before a brief: the chat's own facts (title, mode, the Lead's model,
// usage) and "Crew: Not assigned". Once the chat has started a job: that job's crew, schedule,
// approvals, tools, folders, the crew's models and the job's own usage. Below, the background
// work of every job, with the switch for showing it in the conversation too.

import { useEffect, useRef } from "react";
import { usageText } from "../store/conversation";
import { modelLabel } from "../store/models";
import { useStore } from "../store/store";

export function Inspector() {
  const s = useStore();
  const conversation = s.current ? s.conversations[s.current] : null;
  const title = (s.current && s.titles[s.current]) || (s.screen === "chat" ? "New chat" : "");
  const chat = s.chats.find((c) => c.name === s.chatOn);
  const jobIds = chat?.jobs ?? [];
  // Only this chat's jobs: a new chat (no id yet) must not match the jobs started from no chat.
  const id = chat?.id ?? null;
  const job = [...s.jobs].reverse().find((j) => jobIds.includes(j.job_id) || (!!id && j.chat === id));
  const crews = s.summary?.crews ?? [];
  const jobCrews = job ? crews.filter((c) => job.crews.includes(c.name)) : [];
  // The models the job's crew members work with, each once, with who uses it.
  const byModel = new Map<string, string[]>();
  for (const r of jobCrews.flatMap((c) => c.roles)) {
    if (!r.model) continue;
    const label = modelLabel(r.model, s.models);
    byModel.set(label, [...(byModel.get(label) ?? []), r.title]);
  }
  const jobModels = [...byModel].map(([m, who]) => `${m} (${who.join(", ")})`);
  const tools = Array.from(new Set(jobCrews.flatMap((c) => c.roles.flatMap((r) => r.tools ?? [])))).sort();
  const waiting = job ? s.approvals.filter((a) => a.job_id === job.job_id).length : 0;
  const schedule = job ? s.schedules.find((x) => job.job_id.startsWith(x.spec.replace(/^specs\//, "").replace(/\.md$/, "") + "-")) : undefined;
  const name = s.summary?.name;
  const subtitle = job
    ? `${job.name} · ${job.active ? "running" : (job.status ?? "").replace(/_/g, " ")}`
    : conversation?.mode === "Brief"
      ? "Writing a brief"
      : conversation?.kind === "role.add"
        ? "Adding a crew member"
      : `Pre-brief chat${name ? ` with ${name}` : ""}`;

  return (
    <aside className="inspector" aria-label="Inspector">
      {s.screen === "chat" && (
        <>
          <section>
            <h2>{title}</h2>
            <p className="muted">{subtitle}</p>
          </section>
          <section>
            <b>Crew:</b> {jobCrews.length ? jobCrews.map((c) => c.title).join(", ") : "Not assigned"}
          </section>
          {job && (
            <>
              <section>
                <b>Schedule:</b> {schedule ? schedule.when : "Not scheduled"}
              </section>
              <section>
                <b>Approvals required:</b>{" "}
                {waiting ? (
                  <button className="link-button" onClick={() => s.show("approvals")}>
                    {waiting}
                  </button>
                ) : (
                  0
                )}
              </section>
              <section>
                <b>Available tools:</b>
                <ul className="small">
                  {tools.map((t) => (
                    <li key={t}>{t}</li>
                  ))}
                  {!tools.length && <li>None</li>}
                </ul>
              </section>
              <section>
                <b>Working folder:</b> {job.path.split("/").slice(-1)[0]}/workspace
              </section>
            </>
          )}
          {job ? (
            <section>
              <b>Models:</b>
              <ul className="small">
                {jobModels.map((m) => (
                  <li key={m}>{m}</li>
                ))}
                {!jobModels.length && <li>None</li>}
              </ul>
              <p>
                <b>Usage:</b> {job.total_tokens.toLocaleString()} tokens
              </p>
            </section>
          ) : (
            <section>
              <b>Model:</b> {modelLabel(s.summary?.model, s.models) || "None"}
              <p>
                <b>Usage:</b> {usageText(conversation?.usage ?? null).replace(/^Usage: /, "") || "0%"}
              </p>
            </section>
          )}
        </>
      )}
      <Feed />
    </aside>
  );
}

function Feed() {
  const { feed, prefs, setPref } = useStore();
  const on = prefs?.background_feed !== false;
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [feed.length]);
  return (
    <section className="feed">
      <h2>Background work</h2>
      <label className="switch">
        <span>Updates in main chat are {on ? "on" : "off"}</span>
        <input type="checkbox" role="switch" checked={on} onChange={(e) => void setPref("background_feed", e.target.checked)} />
      </label>
      <div className="feed-lines" aria-live="polite">
        {feed.length === 0 && <p className="muted">Nothing running just now.</p>}
        {feed.map((f) => (
          <p key={f.key} className={f.status === "running" ? "" : "ended-line"}>
            {f.status === "running" ? f.line : `${f.name}: ${f.line}`}
          </p>
        ))}
        <div ref={end} />
      </div>
    </section>
  );
}
