// Settings › General (design 09): what to call you and your profile; model providers (add, edit,
// delete: C5); the default model, the token usage limit and the approval mode; the theme.

import { useEffect, useState } from "react";
import type { Connection, ModelList, Preferences, ProviderPreset } from "../../api/types";
import { useStore, type ThemeChoice } from "../../store/store";
import { Field, Item, Problem, RemovePanel, useAction, useRemoval, useView, type Check } from "./common";
import { ServerForm } from "../Connect";
import { describe, useThisComputer } from "../../where";
import { appVersion, openLink } from "../../platform";
import { inTauri } from "../../api/serve";

export const LIMITS: [string, string][] = [
  ["conservative", "Light: up to 300k tokens a job"],
  ["moderate", "Moderate: up to 800k tokens a job"],
  ["generous", "Generous: up to 2 million tokens a job"],
  ["unlimited", "No limit"],
];

export const MODES: [string, string, string][] = [
  ["manual", "Careful", "Crews can look things up. Anything that changes or sends something waits for your approval."],
  ["balanced", "Balanced", "Crews can change things in your own tools. Anything that reaches people outside, or spends money, waits for your approval."],
  ["auto", "Hands-off", "Crews do everything without asking. Use with care."],
];

const THEMES: [ThemeChoice, string][] = [
  ["auto", "Same as the system"],
  ["light", "Light"],
  ["dark", "Dark"],
];

function Saved({ when }: { when: number }) {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (!when) return;
    setShown(true);
    const t = setTimeout(() => setShown(false), 2000);
    return () => clearTimeout(t);
  }, [when]);
  return shown ? <span className="saved">Saved</span> : null;
}

function About() {
  const { run, problem, busy } = useAction();
  const prefs = useView<Preferences>("/preferences");
  const [name, setName] = useState<string | null>(null);
  const [profile, setProfile] = useState<string | null>(null);
  const [saved, setSaved] = useState(0);
  const p = prefs.data;
  if (!p) return <Problem text={prefs.problem} />;
  const nameNow = name ?? p.name;
  const profileNow = profile ?? p.instructions;
  return (
    <section className="settings-section">
      <Field label="What should Ombro call you?">
        <input className="text-input short" value={nameNow} onChange={(e) => setName(e.target.value)} maxLength={60} />
      </Field>
      <Field label="Your profile" hint="Who you are and how you like to work. The Lead reads this before every chat.">
        <textarea className="text-area" rows={8} value={profileNow} onChange={(e) => setProfile(e.target.value)} />
      </Field>
      <div className="form-actions">
        <button
          className="primary"
          disabled={busy || (nameNow === p.name && profileNow === p.instructions)}
          onClick={async () => {
            const next = await run((api) => api.put<Preferences>("/preferences", { name: nameNow, instructions: profileNow }));
            if (next) {
              prefs.set(next);
              setName(null);
              setProfile(null);
              setSaved(Date.now());
              void useStore.getState().refresh(); // the greeting uses the name
            }
          }}
        >
          Save
        </button>
        <Saved when={saved} />
      </div>
      <Problem text={problem} />
    </section>
  );
}

const OTHER = "other";

function ProviderForm({ current, presets = [], onDone, onCancel }: {
  current: Connection | null;
  presets?: ProviderPreset[];
  onDone: (m: ModelList) => void;
  onCancel: () => void;
}) {
  const { run, problem, busy } = useAction();
  const remote = useStore((s) => s.remote);
  const here = remote ? "the server" : "this computer";
  // Adding: pick a known provider and its address is filled in, or "Another provider" to type one.
  const [choice, setChoice] = useState(current ? OTHER : (presets[0]?.endpoint ?? OTHER));
  const preset = presets.find((p) => p.endpoint === choice) ?? null;
  const [endpoint, setEndpoint] = useState(current?.endpoint ?? presets[0]?.endpoint ?? "");
  const [key, setKey] = useState("");
  const [model, setModel] = useState("");
  const [askModel, setAskModel] = useState(false);
  const needsKey = current !== null || preset === null || preset.key !== "";
  function pick(value: string) {
    setChoice(value);
    const p = presets.find((x) => x.endpoint === value);
    setEndpoint(p ? p.endpoint : "");
    setAskModel(false);
    setModel(p?.model ?? "");
  }
  async function save() {
    const m = current
      ? await run((api) =>
          api.put<ModelList>("/models/providers", {
            endpoint: current.endpoint,
            new_endpoint: endpoint.trim() !== current.endpoint ? endpoint.trim() : undefined,
            key: key || undefined,
          }),
        )
      : await run((api) =>
          api.post<ModelList>("/models/providers", {
            endpoint: endpoint.trim(),
            key: needsKey ? key : "",
            key_name: preset?.key || undefined,
            model,
          }),
        );
    if (m) onDone(m);
  }
  useEffect(() => {
    if (/didn't list its models/.test(problem)) setAskModel(true);
  }, [problem]);
  return (
    <div className="inline-form">
      <h3>{current ? `Edit ${current.label}` : "Add a model provider"}</h3>
      {!current && presets.length > 0 && (
        <Field label="Provider">
          <span className="form-row tight">
            <select aria-label="Provider" value={choice} onChange={(e) => pick(e.target.value)}>
              {presets.map((p) => (
                <option key={p.endpoint} value={p.endpoint}>
                  {remote ? p.label.replace(", on this computer", ", on the server") : p.label}
                </option>
              ))}
              <option value={OTHER}>Another provider</option>
            </select>
          </span>
        </Field>
      )}
      <Field
        label="Address"
        hint={preset ? "Filled in for this provider; change it only if yours differs." : "Any provider that works like OpenAI's API: the part before /chat/completions."}
      >
        <input className="text-input" placeholder="https://api.example.com/v1" value={endpoint} onChange={(e) => setEndpoint(e.target.value)} />
      </Field>
      {needsKey ? (
        <Field
          label="API key"
          hint={current?.key ? `Leave empty to keep the key you have. It's kept on ${here} and only sent to this provider.` : `Kept on ${here}, and only ever sent to this provider. Leave empty if it needs none.`}
        >
          <input className="text-input" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} />
        </Field>
      ) : (
        <p className="muted">{remote ? "It runs on the server, next to Ombro, so it needs no key. Start it there before you add it." : "It runs on this computer, so it needs no key. Start it before you add it."}</p>
      )}
      {!current && askModel && (
        <Field label="Model name" hint="This provider didn't list its models, so name one it offers.">
          <input className="text-input short" value={model} onChange={(e) => setModel(e.target.value)} />
        </Field>
      )}
      <div className="form-actions">
        <button className="primary" disabled={busy || !endpoint.trim()} onClick={() => void save()}>
          {busy ? "Connecting…" : current ? "Save" : "Add provider"}
        </button>
        <button className="secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
      <Problem text={problem} />
    </div>
  );
}

function Providers({ models, onChange }: { models: ModelList; onChange: (m: ModelList) => void }) {
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const removal = useRemoval();
  const { run } = useAction();
  const providers = models.connections.filter((c) => c.endpoint);
  return (
    <section className="settings-section">
      <h2>Model providers</h2>
      <div className="rows">
        {providers.map((c) => (
          <div className="row-block" key={c.endpoint!}>
            <Item
              name={c.label}
              details={[
                c.endpoint,
                <span title={c.models.map((m) => m.model).join(", ")}>
                  {c.models.length === 1 ? "1 model" : `${c.models.length} models`}
                </span>,
              ]}
              actions={
                <>
                  <button className="text-button strong" onClick={() => { setEditing(c.endpoint); setAdding(false); }}>
                    Edit
                  </button>
                  <button
                    className="text-button strong"
                    onClick={() => void removal.ask(c.endpoint!, async () => {
                      const api = useStore.getState().api!;
                      return api.post<Check>("/models/providers/check", { endpoint: c.endpoint });
                    })}
                  >
                    Delete
                  </button>
                </>
              }
            />
            {editing === c.endpoint && (
              <ProviderForm current={c} onCancel={() => setEditing(null)} onDone={(m) => { onChange(m); setEditing(null); }} />
            )}
            {removal.target === c.endpoint && (
              <RemovePanel
                removal={removal}
                what={c.label}
                onConfirm={async () => {
                  const m = await run((api) => api.post<ModelList>("/models/providers/remove", { endpoint: c.endpoint }));
                  if (m) onChange(m);
                  return !!m;
                }}
              />
            )}
          </div>
        ))}
      </div>
      {adding ? (
        <ProviderForm
          current={null}
          presets={(models.presets ?? []).filter((p) => !providers.some((c) => c.endpoint === p.endpoint))}
          onCancel={() => setAdding(false)} onDone={(m) => { onChange(m); setAdding(false); }} />
      ) : (
        <div className="form-actions">
          <button className="secondary" onClick={() => { setAdding(true); setEditing(null); }}>
            Add provider
          </button>
        </div>
      )}
    </section>
  );
}

function Choices({ models, onChange }: { models: ModelList; onChange: (m: ModelList) => void }) {
  const { run, problem } = useAction();
  const prefs = useView<Preferences>("/preferences");
  const limits = useView<{ allowance: string }>("/limits");
  const { themeChoice, setThemeChoice } = useStore();
  const [saved, setSaved] = useState<Record<string, number>>({});
  const mark = (k: string) => setSaved((s) => ({ ...s, [k]: Date.now() }));
  const all = models.connections.flatMap((c) => c.models.map((m) => ({ ...m, label: c.label })));
  const current = all.find((m) => m.default)?.name ?? "";
  const mode = prefs.data?.outward ?? "balanced";
  return (
    <section className="settings-section">
      <Field label="Default model" hint="The Lead and every crew member without a model of its own use it.">
        <span className="form-row tight">
          <select
            aria-label="Default model"
            value={current}
            onChange={async (e) => {
              const m = await run((api) => api.put<ModelList>("/models/default", { name: e.target.value }));
              if (m) { onChange(m); mark("model"); }
            }}
          >
            {all.map((m) => (
              <option key={m.name} value={m.name}>
                {m.model} ({m.label})
              </option>
            ))}
          </select>
          <Saved when={saved.model ?? 0} />
        </span>
      </Field>
      <Field label="Token usage limit" hint="How far each job may go before it stops. Chats and interviews have their own, in step.">
        <span className="form-row tight">
          <select
            aria-label="Token usage limit"
            value={limits.data?.allowance ?? "generous"}
            onChange={async (e) => {
              const l = await run((api) => api.put<{ allowance: string }>("/limits", { allowance: e.target.value }));
              if (l) { limits.set(l); mark("limit"); }
            }}
          >
            {LIMITS.map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </select>
          <Saved when={saved.limit ?? 0} />
        </span>
      </Field>
      <Field label="Approval mode" hint={MODES.find(([v]) => v === mode)?.[2]}>
        <span className="form-row tight">
          <select
            aria-label="Approval mode"
            value={mode}
            onChange={async (e) => {
              const p = await run((api) => api.put<Preferences>("/preferences", { outward: e.target.value }));
              if (p) { prefs.set(p); mark("mode"); }
            }}
          >
            {MODES.map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </select>
          <Saved when={saved.mode ?? 0} />
        </span>
      </Field>
      <Field label="Appearance">
        <span className="form-row tight">
          <select aria-label="Appearance" value={themeChoice} onChange={(e) => setThemeChoice(e.target.value as ThemeChoice)}>
            {THEMES.map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </select>
        </span>
      </Field>
      <Problem text={problem} />
    </section>
  );
}

/** Where Ombro runs: this computer or a server. A switch restarts the app on the other one; each
 * has its own settings, crews and jobs, and nothing is copied between them. App only. */
function WhereItRuns() {
  const where = useStore((s) => s.where);
  const connectNow = useStore((s) => s.connectNow);
  const [form, setForm] = useState(false);
  const [asking, setAsking] = useState(false);
  if (!where) return null;
  const local = where.mode === "local";
  return (
    <section className="settings-section">
      <h2>Where Ombro runs</h2>
      <p>
        Ombro runs on <b>{describe(where)}</b>.
        {!local && " Jobs run there, and can't reach this computer's files."}
      </p>
      {form ? (
        <ServerForm onConnected={() => void connectNow()} onCancel={() => setForm(false)} />
      ) : asking ? (
        <div className="inline-form">
          <p>
            Ombro on this computer has its own settings, crews and jobs; nothing is copied from the server.{" "}
            {where.local_home ? "What you set up here before is still here." : "You'll set it up afresh."} Ombro restarts to
            switch.
          </p>
          <div className="form-actions">
            <button
              className="primary"
              onClick={async () => {
                await useThisComputer();
                await connectNow();
              }}
            >
              Use this computer
            </button>
            <button className="secondary" onClick={() => setAsking(false)}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="form-actions">
          <button className="secondary" onClick={() => setForm(true)}>
            {local ? "Connect to a server" : "Connect to a different server"}
          </button>
          {!local && (
            <button className="secondary" onClick={() => setAsking(true)}>
              Use this computer instead
            </button>
          )}
        </div>
      )}
      {local && !form && (
        <p className="muted">
          A server has its own settings, crews and jobs; nothing is copied from this computer. Ombro restarts to switch.
        </p>
      )}
    </section>
  );
}

/** The app's version, and the server's when Ombro runs on one with another version. */
export function Version() {
  const api = useStore((s) => s.api);
  const remote = useStore((s) => s.remote);
  const [app, setApp] = useState("");
  const [server, setServer] = useState("");
  useEffect(() => {
    let gone = false;
    void appVersion().then((v) => !gone && setApp(v));
    void api
      ?.get<{ version?: string }>("/hello")
      .then((h) => !gone && setServer(h.version ?? ""))
      .catch(() => undefined);
    return () => {
      gone = true;
    };
  }, [api]);
  const shown = app || server;
  if (!shown) return null;
  return (
    <section className="settings-section">
      <h2>Version</h2>
      <p>
        Ombro {shown}
        {remote && server && server !== shown && <span className="muted"> · the server runs Ombro {server}</span>}
      </p>
    </section>
  );
}

/** Use your ChatGPT plan (Sign in with ChatGPT): model calls on the plan's models (named …-plan)
 * use the usage included in the person's ChatGPT plan, with no key. Separate from an OpenAI API
 * key, which is a provider above. */
export function ChatGPTPlan({ models, onChange }: { models: ModelList; onChange: (m: ModelList) => void }) {
  const { run, problem } = useAction();
  const remote = useStore((st) => st.remote);
  const [pasting, setPasting] = useState(false);
  const [pasted, setPasted] = useState("");
  const plan = models.chatgpt;
  const conn = models.connections.find((c) => c.api === "chatgpt");
  const removal = useRemoval();
  const reload = async () => {
    const m = await run((api) => api.get<ModelList>("/models"));
    if (m) onChange(m);
  };

  // While the browser is open, look again every two seconds.
  useEffect(() => {
    if (!plan?.signing_in) return;
    const timer = setInterval(() => void reload(), 2000);
    return () => clearInterval(timer);
  }); // eslint-disable-line react-hooks/exhaustive-deps

  if (!plan) return null;

  async function signIn() {
    let port: number | null = null;
    if (remote && inTauri) {
      // Ombro is on a server, but the browser is here: the app catches the answer and hands it over.
      const { invoke } = await import("@tauri-apps/api/core");
      port = await invoke<number>("sign_in_listener");
    }
    const r = await run((api) =>
      api.post<{ url: string | null; problem?: string }>("/models/chatgpt/sign-in", remote ? { hand_over: true, redirect_port: port } : {}),
    );
    if (r?.url) {
      setPasting(remote);
      setPasted("");
      await openLink(r.url);
    }
    await reload();
    if (port !== null && r?.url) {
      const { invoke } = await import("@tauri-apps/api/core");
      const answer = await invoke<string | null>("sign_in_answer", { port, seconds: 600 }).catch(() => null);
      if (answer) await handOver(answer);
    }
  }

  async function handOver(url: string) {
    const ok = await run((api) => api.post("/models/chatgpt/sign-in/answer", { url }));
    if (ok) setPasting(false);
    await reload();
  }

  const signedIn = plan.state === "signed in";
  return (
    <section className="settings-section">
      <h2>Use your ChatGPT plan</h2>
      <p className="lede">
        Sign in with ChatGPT and Ombro's model calls on your plan's models use the usage included in your ChatGPT plan, instead of an
        API key. This is separate from an OpenAI API key, which you add as a provider above.
      </p>
      {plan.welcome && (
        <div className="notice-card" role="status">
          <strong>You're using your ChatGPT plan</strong>
          <p>Model calls on the models ending in -plan use your ChatGPT plan. You can see and limit that use in ChatGPT settings.</p>
          <button className="primary small" onClick={async () => { const m = await run((api) => api.post<ModelList>("/models/chatgpt/welcomed", {})); if (m) onChange(m); }}>
            Got it
          </button>
        </div>
      )}
      {signedIn || conn ? (
        <div className="rows">
          <div className="row-block">
            <Item
              name="ChatGPT plan"
              details={[
                signedIn ? (plan.email ? `Signed in as ${plan.email}` : "Signed in") : "Signed out: these models won't work until you sign in again",
                conn ? <span title={conn.models.map((m) => m.model).join(", ")}>{conn.models.length === 1 ? "1 model" : `${conn.models.length} models`}</span> : null,
                signedIn ? "Using ChatGPT plan" : null,
              ].filter(Boolean)}
              actions={
                <>
                  <button className="text-button strong" onClick={() => void openLink(plan.usage_url)}>Manage usage</button>
                  {signedIn ? (
                    <button className="text-button strong" onClick={async () => { const m = await run((api) => api.del<ModelList>("/models/chatgpt/sign-in")); if (m) onChange(m); }}>
                      Sign out
                    </button>
                  ) : (
                    <button className="text-button strong" disabled={plan.signing_in} onClick={() => void signIn()}>Sign in</button>
                  )}
                  {conn && (
                    <button
                      className="text-button strong"
                      onClick={() => void removal.ask("chatgpt", async () => useStore.getState().api!.post<Check>("/models/providers/check", { endpoint: "chatgpt" }))}
                    >
                      Delete
                    </button>
                  )}
                </>
              }
            />
            {removal.target === "chatgpt" && (
              <RemovePanel
                removal={removal}
                what="the ChatGPT plan"
                onConfirm={async () => {
                  const m = await run((api) => api.post<ModelList>("/models/providers/remove", { endpoint: "chatgpt" }));
                  if (m) onChange(m);
                  return !!m;
                }}
              />
            )}
          </div>
        </div>
      ) : (
        <div className="form-actions">
          <button className="chatgpt-button" disabled={plan.signing_in} onClick={() => void signIn()}>
            Continue with ChatGPT
          </button>
        </div>
      )}
      {plan.signing_in && <p className="hint">Waiting for you to continue with ChatGPT, in your browser…</p>}
      {plan.signing_in && pasting && (
        <details className="paste-answer">
          <summary>Signed in, but Ombro is still waiting?</summary>
          <Field label="Paste the address your browser ended up on" hint="Copy it from the browser's address bar after signing in.">
            <span className="form-row tight">
              <input className="text-input" value={pasted} onChange={(e) => setPasted(e.target.value)} />
              <button className="secondary" disabled={!pasted.trim()} onClick={() => void handOver(pasted)}>Finish</button>
            </span>
          </Field>
        </details>
      )}
      <Problem text={plan.problem ?? problem} />
    </section>
  );
}

export function General() {
  const models = useView<ModelList>("/models");
  return (
    <>
      <h1 className="page-title">General settings</h1>
      <About />
      <WhereItRuns />
      {models.data ? (
        <>
          <Providers models={models.data} onChange={models.set} />
          <ChatGPTPlan models={models.data} onChange={models.set} />
          <Choices models={models.data} onChange={models.set} />
        </>
      ) : (
        <Problem text={models.problem} />
      )}
      <Version />
    </>
  );
}
