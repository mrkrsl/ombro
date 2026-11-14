// Scheduled work (design 07): each schedule's name, its spec, and when it runs, in words. On or off,
// Edit (when it runs, in place) and Delete (moved aside, never erased). New scheduled job: a saved
// spec, when, and a name.

import { useState } from "react";
import { ConfirmButton } from "../components/ConfirmButton";
import { useStore } from "../store/store";

const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
type Often = "daily" | "weekdays" | "weekly" | "monthly" | "cron";

/** The cron expression for a choice of how often and when. */
export function cronFor(often: Often, time: string, day: number, cron: string): string {
  const [h, m] = time.split(":").map((x) => Number(x) || 0);
  if (often === "daily") return `${m} ${h} * * *`;
  if (often === "weekdays") return `${m} ${h} * * 1-5`;
  if (often === "weekly") return `${m} ${h} * * ${day}`;
  if (often === "monthly") return `${m} ${h} ${Math.min(Math.max(day, 1), 28)} * *`;
  return cron.trim();
}

function When({ onSave, onCancel, saveLabel }: { onSave: (cron: string) => void; onCancel: () => void; saveLabel: string }) {
  const [often, setOften] = useState<Often>("weekdays");
  const [time, setTime] = useState("06:00");
  const [day, setDay] = useState(1);
  const [cron, setCron] = useState("");
  return (
    <div className="form-row">
      <select aria-label="How often" value={often} onChange={(e) => setOften(e.target.value as Often)}>
        <option value="daily">Every day</option>
        <option value="weekdays">Weekdays</option>
        <option value="weekly">Every week on</option>
        <option value="monthly">Every month on day</option>
        <option value="cron">A cron expression</option>
      </select>
      {often === "weekly" && (
        <select aria-label="Day" value={day} onChange={(e) => setDay(Number(e.target.value))}>
          {DAYS.map((d, i) => (
            <option key={d} value={i}>
              {d}
            </option>
          ))}
        </select>
      )}
      {often === "monthly" && (
        <input aria-label="Day of the month" type="number" min={1} max={28} value={day} onChange={(e) => setDay(Number(e.target.value))} />
      )}
      {often === "cron" ? (
        <input aria-label="Cron expression" placeholder="30 7 * * 1-5" value={cron} onChange={(e) => setCron(e.target.value)} />
      ) : (
        <input aria-label="Time" type="time" value={time} onChange={(e) => setTime(e.target.value)} />
      )}
      <button className="primary small" onClick={() => onSave(cronFor(often, time, day, cron))}>
        {saveLabel}
      </button>
      <button className="text-button" onClick={onCancel}>
        Cancel
      </button>
    </div>
  );
}

export function Scheduled() {
  const { schedules, specs, call, load } = useStore();
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [spec, setSpec] = useState("");

  async function change(fn: Parameters<typeof call>[0]) {
    await call(fn);
    await load("scheduled");
  }

  return (
    <div className="page">
      <div className="column wide-page">
        <h1 className="page-title">Scheduled work</h1>
        {schedules.length === 0 && <p className="muted">Nothing is scheduled.</p>}
        <div className="rows">
          {schedules.map((s) => (
            <div key={s.name} className="row-block">
              <div className="row">
                <span className="row-name">{s.name.replace(/-/g, " ").replace(/^./, (c) => c.toUpperCase())}</span>
                <span className="row-detail" title={s.spec_title}>
                  {s.spec_title || s.spec}
                </span>
                <span className="row-when" title={s.next ? `Next: ${s.next.replace("T", " ")}` : ""}>
                  {s.problem ? `Problem: ${s.problem}` : s.when}
                </span>
                <span className="row-actions">
                  <label className="switch compact" title={s.enabled ? "On" : "Off"}>
                    <input
                      type="checkbox"
                      role="switch"
                      aria-label={`${s.name} on`}
                      checked={s.enabled}
                      onChange={(e) => void change((api) => api.post(`/schedules/${s.name}/${e.target.checked ? "enable" : "disable"}`))}
                    />
                  </label>
                  <button className="text-button strong" onClick={() => setEditing(editing === s.name ? null : s.name)}>
                    Edit
                  </button>
                  <ConfirmButton
                    label="Delete"
                    className="text-button strong"
                    ask="Delete it? Its jobs are kept."
                    onConfirm={() => change((api) => api.del(`/schedules/${s.name}`))}
                  />
                </span>
              </div>
              {editing === s.name && (
                <div className="row-open">
                  <p className="muted">Now: {s.when} ({s.cron})</p>
                  <When
                    saveLabel="Save"
                    onCancel={() => setEditing(null)}
                    onSave={async (cron) => {
                      await change((api) => api.put(`/schedules/${s.name}`, { cron }));
                      setEditing(null);
                    }}
                  />
                </div>
              )}
            </div>
          ))}
        </div>
        {!adding ? (
          <div className="actions">
            <button className="primary" onClick={() => setAdding(true)} disabled={!specs.length}>
              New scheduled job
            </button>
            {!specs.length && <span className="muted">Brief a task first: a schedule starts a saved spec.</span>}
          </div>
        ) : (
          <div className="new-schedule">
            <div className="form-row">
              <input aria-label="Name" placeholder="news-briefing" value={name} onChange={(e) => setName(e.target.value.toLowerCase().replace(/[^a-z0-9_-]+/g, "-"))} />
              <select aria-label="Saved spec" value={spec} onChange={(e) => setSpec(e.target.value)}>
                <option value="">Which saved spec?</option>
                {specs.map((sp) => (
                  <option key={sp.name} value={sp.name}>
                    {sp.title || sp.name}
                  </option>
                ))}
              </select>
            </div>
            <When
              saveLabel="Add"
              onCancel={() => setAdding(false)}
              onSave={async (cron) => {
                if (!name || !spec) return;
                await change((api) => api.post("/schedules", { name, spec, cron }));
                setAdding(false);
                setName("");
                setSpec("");
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
