// Settings › General: what to call you and your profile; model providers (add, edit,
// delete); the default model, the token usage limit and the approval mode; the theme.

import { useEffect, useState, type ReactNode } from "react";
import type { BrowserView, Connection, ModelList, Preferences, ProviderPreset } from "../../api/types";
import { useStore, type ThemeChoice } from "../../store/store";
import { Field, Item, Problem, RemovePanel, useAction, useRemoval, useView, type Check } from "./common";
import { ServerForm } from "../Connect";
import { describe, useThisComputer } from "../../where";
import { appVersion, catchSignIn, chooseBrowser, openLink } from "../../platform";
import { inTauri } from "../../api/serve";
import { autoCheck, checkForUpdate, setAutoCheck } from "../../updates";
import { ModelOptions } from "../../components/ModelOptions";
import { modelChoices } from "../../store/models";

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

export const THINKING: [string, string][] = [
  ["off", "No thinking"],
  ["low", "Low"],
  ["medium", "Medium"],
  ["high", "High"],
  ["xhigh", "Extra high"],
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
const PLAN = "chatgpt"; // the ChatGPT plan in the Provider picker, and where an address would go

/** Signing in with ChatGPT, so model calls use the person's ChatGPT plan (no key). When Ombro is on
 * a server, the browser comes back to this computer: the app catches the answer and hands it over,
 * or the address is pasted. */
function usePlanSignIn(onChange: (m: ModelList) => void) {
  const { run, problem } = useAction();
  const remote = useStore((st) => st.remote);
  const [pasting, setPasting] = useState(false);
  const [pasted, setPasted] = useState("");
  const reload = async () => {
    const m = await run((api) => api.get<ModelList>("/models"));
    if (m) onChange(m);
  };
  async function handOver(url: string) {
    const ok = await run((api) => api.post("/models/chatgpt/sign-in/answer", { url }));
    if (ok) setPasting(false);
    await reload();
  }
  async function signIn() {
    let port: number | null = null;
    if (remote && inTauri) {
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
      // Only an answer that brings back this sign-in's state is handed over.
      const answer = await catchSignIn(port, r.url);
      if (answer) await handOver(answer);
    }
  }
  async function signOut() {
    const m = await run((api) => api.del<ModelList>("/models/chatgpt/sign-in"));
    if (m) onChange(m);
  }
  async function welcomed() {
    const m = await run((api) => api.post<ModelList>("/models/chatgpt/welcomed", {}));
    if (m) onChange(m);
  }
  return { signIn, signOut, welcomed, handOver, reload, pasting, pasted, setPasted, problem };
}

type PlanSignIn = ReturnType<typeof usePlanSignIn>;

/** What signing in with ChatGPT shows while it lasts, and the first time it's done. */
function PlanStatus({ plan, signing }: { plan: ModelList["chatgpt"]; signing: PlanSignIn }) {
  return (
    <>
      {plan.welcome && (
        <div className="notice-card" role="status">
          <strong>You're using your ChatGPT plan</strong>
          <p>Model calls on the models ending in -plan use your ChatGPT plan. You can see and limit that use in ChatGPT settings.</p>
          <button className="primary small" onClick={() => void signing.welcomed()}>Got it</button>
        </div>
      )}
      {plan.signing_in && <p className="hint">Waiting for you to continue with ChatGPT, in your browser…</p>}
      {plan.signing_in && signing.pasting && (
        <details className="paste-answer">
          <summary>Signed in, but Ombro is still waiting?</summary>
          <Field label="Paste the address your browser ended up on" hint="Copy it from the browser's address bar after signing in.">
            <span className="form-row tight">
              <input className="text-input" value={signing.pasted} onChange={(e) => signing.setPasted(e.target.value)} />
              <button className="secondary" disabled={!signing.pasted.trim()} onClick={() => void signing.handOver(signing.pasted)}>Finish</button>
            </span>
          </Field>
        </details>
      )}
      <Problem text={plan.problem ?? signing.problem} />
    </>
  );
}

function ProviderForm({ current, presets = [], plan = null, onDone, onCancel }: {
  current: Connection | null;
  presets?: ProviderPreset[];
  /** Signing in with ChatGPT, offered in the picker when the plan isn't connected yet. */
  plan?: { busy: boolean; signIn: () => void } | null;
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
              {presets.flatMap((p) => {
                const option = (
                  <option key={p.endpoint} value={p.endpoint}>
                    {remote ? p.label.replace(", on this computer", ", on the server") : p.label}
                  </option>
                );
                // The ChatGPT plan sits next to the OpenAI API key: two ways to use OpenAI's models.
                return plan && p.endpoint === "https://api.openai.com/v1"
                  ? [option, <option key={PLAN} value={PLAN}>ChatGPT plan (sign in, no key)</option>]
                  : [option];
              })}
              {plan && !presets.some((p) => p.endpoint === "https://api.openai.com/v1") && (
                <option value={PLAN}>ChatGPT plan (sign in, no key)</option>
              )}
              <option value={OTHER}>Another provider</option>
            </select>
          </span>
        </Field>
      )}
      {choice === PLAN && plan ? (
        <>
          <p className="muted">
            Sign in with ChatGPT and model calls on its models use the usage included in your ChatGPT plan, with no API key. You can
            see and limit that use in ChatGPT settings.
          </p>
          <div className="form-actions">
            <button className="chatgpt-button" disabled={plan.busy} onClick={() => plan.signIn()}>
              Continue with ChatGPT
            </button>
            <button className="secondary" onClick={onCancel}>
              Cancel
            </button>
          </div>
        </>
      ) : (
        <>
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
        </>
      )}
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
  const plan = models.chatgpt;
  const planConn = models.connections.find((c) => c.api === "chatgpt");
  const planSignedIn = plan?.state === "signed in";
  const signing = usePlanSignIn(onChange);
  // While the browser is open, look again every two seconds.
  useEffect(() => {
    if (!plan?.signing_in) return;
    const timer = setInterval(() => void signing.reload(), 2000);
    return () => clearInterval(timer);
  }); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <section className="settings-section">
      <h2>Model providers</h2>
      <div className="rows">
        {plan && (planConn || planSignedIn) && (
          <div className="row-block" key={PLAN}>
            <Item
              name="ChatGPT plan"
              details={[
                planSignedIn ? (plan.email ? `Signed in as ${plan.email}` : "Signed in") : "Signed out: its models won't work until you sign in again",
                planConn ? (
                  <span title={planConn.models.map((m) => m.model).join(", ")}>
                    {planConn.models.length === 1 ? "1 model" : `${planConn.models.length} models`}
                  </span>
                ) : null,
                planSignedIn ? "Using ChatGPT plan" : null,
              ]}
              actions={
                <>
                  <button className="text-button strong" onClick={() => void openLink(plan.usage_url)}>Manage usage</button>
                  {planSignedIn ? (
                    <button className="text-button strong" onClick={() => void signing.signOut()}>Sign out</button>
                  ) : (
                    <button className="text-button strong" disabled={plan.signing_in} onClick={() => void signing.signIn()}>Sign in</button>
                  )}
                  {planConn && (
                    <button
                      className="text-button strong"
                      onClick={() => void removal.ask(PLAN, async () => useStore.getState().api!.post<Check>("/models/providers/check", { endpoint: PLAN }))}
                    >
                      Delete
                    </button>
                  )}
                </>
              }
            />
            {removal.target === PLAN && (
              <RemovePanel
                removal={removal}
                what="the ChatGPT plan"
                onConfirm={async () => {
                  const m = await run((api) => api.post<ModelList>("/models/providers/remove", { endpoint: PLAN }));
                  if (m) onChange(m);
                  return !!m;
                }}
              />
            )}
          </div>
        )}
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
      {plan && <PlanStatus plan={plan} signing={signing} />}
      {adding ? (
        <ProviderForm
          current={null}
          presets={(models.presets ?? []).filter((p) => !providers.some((c) => c.endpoint === p.endpoint))}
          plan={plan && !planConn && !planSignedIn ? { busy: plan.signing_in, signIn: () => { void signing.signIn(); setAdding(false); } } : null}
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

/** A setting under its section's heading: the control and its hint, with no label of its own. */
function Setting({ hint, children }: { hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="field">
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </div>
  );
}

function Choices({ models, onChange }: { models: ModelList; onChange: (m: ModelList) => void }) {
  const { run, problem } = useAction();
  const prefs = useView<Preferences>("/preferences");
  const limits = useView<{ allowance: string }>("/limits");
  const browser = useView<BrowserView>("/browser");
  const { themeChoice, setThemeChoice, remote } = useStore();
  const [other, setOther] = useState<string | null>(null); // "Other…": the path being typed
  const [checking, setChecking] = useState(false);
  const [saved, setSaved] = useState<Record<string, number>>({});
  const mark = (k: string) => setSaved((s) => ({ ...s, [k]: Date.now() }));
  const current = modelChoices(models).find((m) => m.default)?.name ?? "";
  const mode = prefs.data?.outward ?? "balanced";
  return (
    <>
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
            <ModelOptions models={models} />
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
      <Field
        label="Thinking"
        hint="How much models think before they answer. More is slower and costs more; models that don't think are left alone. A model can have its own level in models.md."
      >
        <span className="form-row tight">
          <select
            aria-label="Thinking"
            value={prefs.data?.thinking ?? "low"}
            onChange={async (e) => {
              const p = await run((api) => api.put<Preferences>("/preferences", { thinking: e.target.value }));
              if (p) { prefs.set(p); mark("thinking"); }
            }}
          >
            {THINKING.map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </select>
          <Saved when={saved.thinking ?? 0} />
        </span>
      </Field>
    </section>
    {browser.data && (() => {
      const b = browser.data;
      const listed = ["auto", "off", ...b.installed.map((x) => x.path)];
      const chosenOther = !listed.includes(b.choice); // a path given with Other…
      const choose = async (choice: string) => {
        const v = await run((api) => api.put<BrowserView>("/browser", { choice }));
        if (v) { browser.set(v); mark("browser"); setOther(null); }
        return v;
      };
      return (
      <section className="settings-section">
        <h2>Browser</h2>
        <Setting
          hint={
            b.problem ??
            `Crews and chat read web pages in ${b.using} without a window, with a new, empty profile each time. Forms with your details wait for your approval.`
          }
        >
          <span className="form-row tight">
            <select
              aria-label="Browser"
              value={other !== null ? "other" : b.choice}
              onChange={async (e) => {
                if (e.target.value === "other") { setOther(chosenOther ? b.choice : ""); return; }
                await choose(e.target.value);
              }}
            >
              <option value="auto">
                Automatic{b.choice === "auto" && b.using ? ` (${b.using})` : ""}
              </option>
              {b.installed.map((x) => (
                <option key={x.path} value={x.path}>
                  {x.name}
                </option>
              ))}
              {chosenOther && <option value={b.choice}>{b.using ?? b.choice}</option>}
              <option value="other">Other…</option>
              <option value="off">Don't use a browser</option>
            </select>
            <Saved when={saved.browser ?? 0} />
          </span>
        </Setting>
        {other !== null && (
          <Field
            label={remote ? "Path of the browser on the server" : "Path of the browser"}
            hint={
              (remote
                ? "Jobs run on the server, so the browser must be installed there. "
                : "") +
              "Any browser built on Chromium works (Opera, Arc, Thorium…); Firefox and Safari don't. Ombro starts it once to check."
            }
          >
            <span className="form-row">
              <input
                aria-label="Browser path"
                value={other}
                placeholder={remote ? "/usr/bin/vivaldi" : "/Applications/Helium.app"}
                onChange={(e) => setOther(e.target.value)}
              />
              {!remote && inTauri && (
                <button
                  className="secondary"
                  onClick={async () => { const p = await chooseBrowser(); if (p) setOther(p); }}
                >
                  Choose…
                </button>
              )}
              <button
                className="primary"
                disabled={!other.trim() || checking}
                onClick={async () => { setChecking(true); try { await choose(other.trim()); } finally { setChecking(false); } }}
              >
                {checking ? "Checking…" : "Use this browser"}
              </button>
              <button className="secondary" onClick={() => setOther(null)}>Cancel</button>
            </span>
          </Field>
        )}
        {other !== null && <Problem text={problem} />}
      </section>
      );
    })()}
    <section className="settings-section">
      <h2>Approval mode</h2>
      <Setting hint={MODES.find(([v]) => v === mode)?.[2]}>
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
      </Setting>
    </section>
    <section className="settings-section">
      <h2>Appearance</h2>
      <Setting>
        <span className="form-row tight">
          <select aria-label="Appearance" value={themeChoice} onChange={(e) => setThemeChoice(e.target.value as ThemeChoice)}>
            {THEMES.map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </select>
        </span>
      </Setting>
      {other === null && <Problem text={problem} />}
    </section>
    </>
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
  const [auto, setAuto] = useState(autoCheck);
  const shown = app || server;
  if (!shown) return null;
  return (
    <section className="settings-section">
      <h2>Version</h2>
      <p>
        Ombro {shown}
        {remote && server && server !== shown && <span className="muted"> · the server runs Ombro {server}</span>}
      </p>
      {app && (
        <Setting>
          <span className="form-row tight">
            <label className="switch">
              <span>Check for updates automatically</span>
              <input
                type="checkbox"
                role="switch"
                checked={auto}
                onChange={(e) => {
                  setAutoCheck(e.target.checked);
                  setAuto(e.target.checked);
                }}
              />
            </label>
            <button className="secondary small" onClick={() => void checkForUpdate()}>
              Check now
            </button>
          </span>
        </Setting>
      )}
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
          <Choices models={models.data} onChange={models.set} />
        </>
      ) : (
        <Problem text={models.problem} />
      )}
      <Version />
    </>
  );
}
