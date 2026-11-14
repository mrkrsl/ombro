// Settings › Tools (designs 13–15): one list of the tools crews can use. The built-in web tools with
// their provider and call limit; then each MCP server, online (an address, with its sign-in) or on
// this computer (a command). Add, edit, delete, and sign in (the existing OAuth flow, in the browser).
// The forms ask what the core needs (plan 2). Secrets are written, never read back: an edit shows
// their names, and a value left empty keeps the one stored.

import { useEffect, useState } from "react";
import type { KeyList, NativeTool, ServerRow, ToolList } from "../../api/types";
import { openLink } from "../../platform";
import { useStore } from "../../store/store";
import { Field, Problem, RemovePanel, tidy, useAction, useRemoval, useView, type Check } from "./common";

export const NATIVE: Record<string, string> = { "web.search": "Web search", "web.fetch": "Reading web pages" };
export const PROVIDERS: Record<string, string> = {
  duckduckgo: "DuckDuckGo (free)",
  direct: "Directly (free)",
  firecrawl: "Firecrawl (paid, needs a key)",
};
const CHOICES: Record<string, string[]> = { "web.search": ["duckduckgo", "firecrawl"], "web.fetch": ["direct", "firecrawl"] };
const NEEDS_KEY: Record<string, string> = { firecrawl: "FIRECRAWL_API_KEY" };

type Kind = "remote" | "local" | "builtin";
type Secret = { name: string; value: string; kept: boolean };

/** How a server's sign-in reads, and whether it needs one now. */
export function signInText(s: ServerRow): [string, boolean] {
  if (s.signing_in) return ["Waiting for you to sign in, in your browser…", false];
  if (s.problem) return [s.problem, true];
  if (!s.url) return ["On this computer", false];
  const st = s.sign_in ?? "";
  if (st === "not signed in") return ["Not signed in", true];
  if (st === "sign-in expired") return ["Sign-in expired", true];
  return [st.charAt(0).toUpperCase() + st.slice(1), false];
}

function where(s: ServerRow): string {
  return s.url ?? [s.command, ...s.args].join(" ");
}

function Secrets({ rows, setRows, remote }: { rows: Secret[]; setRows: (r: Secret[]) => void; remote: boolean }) {
  const what = remote ? "header" : "setting";
  return (
    <div className="secrets">
      <span className="field-label">{remote ? "Secret headers (optional)" : "Secret settings (optional)"}</span>
      <span className="field-hint">
        {remote
          ? "If the service gives you a key, add it here, for example Authorization and the key. It's kept on this computer."
          : "If the program needs a key, add it here, for example API_KEY and the key. It's kept on this computer."}
      </span>
      {rows.map((r, i) => (
        <span className="form-row tight" key={i}>
          <input
            aria-label={`Secret ${what} name`}
            placeholder={remote ? "Authorization" : "API_KEY"}
            value={r.name}
            disabled={r.kept}
            onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))}
          />
          <input
            aria-label={`Secret ${what} value`}
            type="password"
            autoComplete="off"
            placeholder={r.kept ? "Kept: type to replace" : "Value"}
            value={r.value}
            onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))}
          />
          <button className="text-button strong" onClick={() => setRows(rows.filter((_, j) => j !== i))}>
            Remove
          </button>
        </span>
      ))}
      <button className="text-button strong add-secret" onClick={() => setRows([...rows, { name: "", value: "", kept: false }])}>
        Add a {what}
      </button>
    </div>
  );
}

function ServerForm({ current, kind, onDone, onCancel }: {
  current: ServerRow | null;
  kind: "remote" | "local";
  onDone: (t: ToolList, signIn: string | null, warning: string | null) => void;
  onCancel: () => void;
}) {
  const { run, problem, busy } = useAction();
  const [name, setName] = useState(current ? current.server : "");
  const [url, setUrl] = useState(current?.url ?? "");
  const [command, setCommand] = useState(current && !current.url ? where(current) : "");
  const [secrets, setSecrets] = useState<Secret[]>(current ? current.secrets.map((n) => ({ name: n, value: "", kept: true })) : []);
  const remote = kind === "remote";

  async function save() {
    const body = {
      name,
      url: remote ? url.trim() : undefined,
      command: remote ? undefined : command.trim(),
      secrets: Object.fromEntries(secrets.filter((s) => s.name.trim() && s.value).map((s) => [s.name.trim(), s.value])),
      keep: secrets.filter((s) => s.kept && !s.value).map((s) => s.name),
    };
    type Reply = ToolList & { warning?: string | null; added?: string };
    const t = current
      ? await run((api) => api.put<Reply>(`/tools/servers/${current.server}`, body))
      : await run((api) => api.post<Reply>("/tools/servers", body));
    if (!t) return;
    const added = current ? current.server : t.added ?? null;
    onDone(t, remote && !current ? added : null, t.warning ?? null);
  }

  return (
    <div className="inline-form">
      <h3>{current ? `Edit ${tidy(current.server)}` : remote ? "Add an online service" : "Add a program on this computer"}</h3>
      {!current && (
        <Field label="Name" hint="A short name crews will know it by, such as notion or sheets.">
          <input className="text-input short" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
      )}
      {remote ? (
        <Field label="Address" hint="The service's MCP address. It must start with https://.">
          <input className="text-input" placeholder="e.g. https://mcp.notion.com/mcp" value={url} onChange={(e) => setUrl(e.target.value)} />
        </Field>
      ) : (
        <Field label="Command" hint="Name an exact version, so it can't change without you knowing.">
          <input className="text-input" placeholder="e.g. uvx some-mcp-server==1.2.3" value={command} onChange={(e) => setCommand(e.target.value)} />
        </Field>
      )}
      <Secrets rows={secrets} setRows={setSecrets} remote={remote} />
      <div className="form-actions">
        <button className="primary" disabled={busy || (!current && !name.trim()) || !(remote ? url.trim() : command.trim())} onClick={() => void save()}>
          {current ? "Save" : remote ? "Authenticate & Add" : "Add"}
        </button>
        <button className="secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
      <Problem text={problem} />
    </div>
  );
}

function BuiltinForm({ tool, tools, onDone, onCancel }: {
  tool: NativeTool | null;
  tools: ToolList;
  onDone: (t: ToolList) => void;
  onCancel: () => void;
}) {
  const { run, problem, busy } = useAction();
  const keyList = useView<KeyList>("/keys");
  const [which, setWhich] = useState(tool?.tool ?? "web.search");
  const now = tools.native.find((t) => t.tool === which);
  const [provider, setProvider] = useState(now?.provider ?? CHOICES[which][0]);
  const [calls, setCalls] = useState(String(now?.calls ?? ""));
  const [key, setKey] = useState("");
  useEffect(() => {
    const t = tools.native.find((x) => x.tool === which);
    setProvider(t?.provider ?? CHOICES[which][0]);
    setCalls(String(t?.calls ?? ""));
  }, [which, tools]);
  const keyName = NEEDS_KEY[provider];
  const keySet = !!keyName && !!keyList.data?.keys.find((k) => k.name === keyName && k.set);
  return (
    <div className="inline-form">
      <h3>{tool ? `Edit ${NATIVE[tool.tool]}` : "Choose how crews search and read the web"}</h3>
      {!tool && (
        <Field label="Tool">
          <span className="form-row tight">
            <select aria-label="Tool" value={which} onChange={(e) => setWhich(e.target.value)}>
              {Object.entries(NATIVE).map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </span>
        </Field>
      )}
      <Field label="Provider">
        <span className="form-row tight">
          <select aria-label="Provider" value={provider} onChange={(e) => setProvider(e.target.value)}>
            {CHOICES[which].map((p) => (
              <option key={p} value={p}>
                {PROVIDERS[p] ?? p}
              </option>
            ))}
          </select>
        </span>
      </Field>
      {keyName && (
        <Field label="Key" hint={keySet ? "A key is set. Leave this empty to keep it." : "It's kept on this computer and only sent to the provider."}>
          <input className="text-input" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} />
        </Field>
      )}
      <Field label="Calls a job may make">
        <input
          className="text-input number"
          type="number"
          min={1}
          aria-label="Calls a job may make"
          value={calls}
          onChange={(e) => setCalls(e.target.value)}
        />
      </Field>
      <div className="form-actions">
        <button
          className="primary"
          disabled={busy || (!!keyName && !keySet && !key)}
          onClick={async () => {
            const n = Number(calls);
            const t = await run((api) =>
              api.put<ToolList>(`/tools/${which}`, {
                provider,
                key: key || undefined,
                max_calls: Number.isInteger(n) && n > 0 && n !== now?.calls ? n : undefined,
              }),
            );
            if (t) onDone(t);
          }}
        >
          Save
        </button>
        <button className="secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
      <Problem text={problem} />
    </div>
  );
}

export function Tools() {
  const tools = useView<ToolList>("/tools");
  const { run, problem } = useAction();
  const removal = useRemoval();
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState<Kind | null>(null);
  const [kind, setKind] = useState<Kind>("remote");
  const [warning, setWarning] = useState<string | null>(null);
  const t = tools.data;
  const waiting = !!t?.servers.some((s) => s.signing_in);

  // While a sign-in waits for the browser, look again every two seconds.
  useEffect(() => {
    if (!waiting) return;
    const timer = setInterval(() => void tools.reload(), 2000);
    return () => clearInterval(timer);
  }, [waiting, tools]);

  async function signIn(server: string) {
    const r = await run((api) => api.post<{ url: string | null; state?: string; problem?: string }>(`/tools/servers/${server}/sign-in`));
    if (r?.url) await openLink(r.url);
    await tools.reload();
  }

  return (
    <>
      <h1 className="page-title">Connected tools</h1>
      <p className="lede">The tools your crews can use. A crew member uses a tool once it's named in their settings.</p>
      <Problem text={tools.problem} />
      {t && (
        <div className="rows">
          {t.native
            .filter((n) => n.tool in NATIVE)
            .map((n) => (
              <div className="row-block" key={n.tool}>
                <div className="row settings-row tool">
                  <span className="row-name">{NATIVE[n.tool]}</span>
                  <span className="row-detail">{(PROVIDERS[n.provider ?? ""] ?? n.provider ?? "").replace(/ \(.*\)$/, "")}</span>
                  <span className="row-detail">{n.calls ? `Up to ${n.calls} calls a job` : ""}</span>
                  <span className="row-actions">
                    <button className="text-button strong" onClick={() => { setEditing(n.tool); setAdding(null); }}>
                      Edit
                    </button>
                  </span>
                </div>
                {editing === n.tool && (
                  <BuiltinForm tool={n} tools={t} onCancel={() => setEditing(null)} onDone={(x) => { tools.set(x); setEditing(null); }} />
                )}
              </div>
            ))}
          {t.servers.map((s) => {
            const [state, needs] = signInText(s);
            return (
              <div className="row-block" key={s.server}>
                <div className="row settings-row tool">
                  <span className="row-name">{tidy(s.server)}</span>
                  <span className="row-detail" title={where(s)}>
                    {where(s)}
                  </span>
                  <span className={`row-detail ${s.problem ? "problem-inline" : ""}`} title={state}>
                    {state}
                  </span>
                  <span className="row-actions">
                    {s.url && needs && !s.signing_in && (
                      <button className="text-button strong" onClick={() => void signIn(s.server)}>
                        Sign in
                      </button>
                    )}
                    <button className="text-button strong" onClick={() => { setEditing(s.server); setAdding(null); }}>
                      Edit
                    </button>
                    <button
                      className="text-button strong"
                      onClick={() => void removal.ask(s.server, () => useStore.getState().api!.get<Check>(`/tools/servers/${s.server}/check`))}
                    >
                      Delete
                    </button>
                  </span>
                </div>
                {editing === s.server && (
                  <ServerForm
                    current={s}
                    kind={s.url ? "remote" : "local"}
                    onCancel={() => setEditing(null)}
                    onDone={(x, _s, w) => { tools.set(x); setEditing(null); setWarning(w); }}
                  />
                )}
                {removal.target === s.server && (
                  <RemovePanel
                    removal={removal}
                    what={tidy(s.server)}
                    onConfirm={async () => {
                      const x = await run((api) => api.del<ToolList>(`/tools/servers/${s.server}`));
                      if (x) tools.set(x);
                      return !!x;
                    }}
                  />
                )}
              </div>
            );
          })}
        </div>
      )}
      {adding === null ? (
        <div className="form-actions">
          <button className="primary" onClick={() => { setAdding(kind); setEditing(null); }}>
            Add a tool
          </button>
        </div>
      ) : (
        <div className="add-tool">
          <h2>Add a tool</h2>
          <Field label="Type">
            <span className="form-row tight">
              <select
                aria-label="Type"
                value={adding}
                onChange={(e) => {
                  setKind(e.target.value as Kind);
                  setAdding(e.target.value as Kind);
                }}
              >
                <option value="remote">An online service (MCP)</option>
                <option value="local">A program on this computer (MCP)</option>
                <option value="builtin">Web search or reading pages (built in)</option>
              </select>
            </span>
          </Field>
          {adding === "builtin" && t ? (
            <BuiltinForm tool={null} tools={t} onCancel={() => setAdding(null)} onDone={(x) => { tools.set(x); setAdding(null); }} />
          ) : (
            <ServerForm
              key={adding}
              current={null}
              kind={adding === "local" ? "local" : "remote"}
              onCancel={() => setAdding(null)}
              onDone={(x, toSignIn, w) => {
                tools.set(x);
                setAdding(null);
                setWarning(w);
                if (toSignIn) void signIn(toSignIn);
              }}
            />
          )}
        </div>
      )}
      {warning && <p className="warning-note">Note: {warning}</p>}
      <Problem text={problem} />
    </>
  );
}
