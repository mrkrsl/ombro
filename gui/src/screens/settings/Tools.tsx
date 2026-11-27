// Settings › Tools (designs 13–15): one list of the tools crews can use. The built-in web tools with
// their provider and call limit; then each MCP server, online (an address, with its sign-in) or on
// this computer (a command). Add, edit, delete, and sign in (the existing OAuth flow, in the browser).
// The forms ask what the core needs. Secrets are written, never read back: an edit shows
// their names, and a value left empty keeps the one stored.

import { useEffect, useState, type ReactNode } from "react";
import type { ApiRow, ComposioAccount, KeyList, NativeTool, ServerRow, ToolList } from "../../api/types";
import { inTauri } from "../../api/serve";
import { catchSignIn, openLink } from "../../platform";
import { useStore } from "../../store/store";
import { Field, Item, Problem, RemovePanel, shortPath, tidy, useAction, useRemoval, useView, type Check } from "./common";

export const NATIVE: Record<string, string> = { "web.search": "Web search", "web.fetch": "Reading web pages" };
export const PROVIDERS: Record<string, string> = {
  duckduckgo: "DuckDuckGo (free)",
  browser: "In the browser (free)",
  searxng: "SearXNG (your own, free)",
  brave: "Brave Search (needs a key)",
  tavily: "Tavily (needs a key)",
  exa: "Exa (needs a key)",
  serper: "Serper (needs a key)",
  firecrawl: "Firecrawl (paid, needs a key)",
  custom: "Another search service",
};
const CHOICES: Record<string, string[]> = {
  "web.search": ["duckduckgo", "firecrawl"],
  "web.fetch": ["browser", "firecrawl"],
};
const NEEDS_KEY: Record<string, string> = { firecrawl: "FIRECRAWL_API_KEY" };

// Web search: in the browser, on a search engine's own page; or a search service by its API.
export const ENGINES: [string, string][] = [
  ["duckduckgo", "DuckDuckGo"],
  ["bing", "Bing"],
  ["ecosia", "Ecosia"],
  ["startpage", "Startpage"],
  ["kagi", "Kagi"],
  ["brave", "Brave Search"],
  ["qwant", "Qwant"],
  ["google", "Google"],
  ["other", "Other…"],
];
export const SERVICES: [string, string][] = [
  ["duckduckgo", "DuckDuckGo (free, no key)"],
  ["searxng", "SearXNG (your own, free)"],
  ["brave", "Brave Search (needs a key)"],
  ["tavily", "Tavily (needs a key)"],
  ["exa", "Exa (needs a key)"],
  ["serper", "Serper (needs a key)"],
  ["firecrawl", "Firecrawl (needs a key)"],
  ["custom", "Other…"],
];
const KEY_SITES: Record<string, string> = {
  brave: "https://api-dashboard.search.brave.com",
  tavily: "https://app.tavily.com",
  exa: "https://dashboard.exa.ai",
  serper: "https://serper.dev",
  firecrawl: "https://www.firecrawl.dev",
};

/** How the row names web search's provider: "Bing in the browser", "Tavily". */
export function searchUsing(n: NativeTool): string {
  if (n.provider === "browser") {
    const e = n.search?.engine ?? "duckduckgo";
    const name = ENGINES.find(([v]) => v === e)?.[1] ?? hostOf(e);
    return `${name} in the browser`;
  }
  if (n.provider === "custom") return n.search?.custom ? hostOf(n.search.custom) : "another search service";
  return (PROVIDERS[n.provider ?? ""] ?? n.provider ?? "").replace(/ \(.*\)$/, "");
}

function hostOf(url: string): string {
  try {
    return new URL(url.replace("{query}", "q").replace("%s", "q")).hostname;
  } catch {
    return url;
  }
}

function SearchForm({ now, picker, browserFound, onDone, onCancel }: {
  now: NativeTool | undefined;
  picker: ReactNode;
  browserFound: boolean;
  onDone: (t: ToolList) => void;
  onCancel: () => void;
}) {
  const { run, problem, busy } = useAction();
  const here = useStore((st) => (st.remote ? "the server" : "this computer"));
  const s = now?.search;
  const known = (e: string) => ENGINES.some(([v]) => v === e && v !== "other");
  const [mode, setMode] = useState<"browser" | "service">(now?.provider === "browser" ? "browser" : "service");
  const [engine, setEngine] = useState(s && !known(s.engine) ? "other" : s?.engine ?? "duckduckgo");
  const [otherEngine, setOtherEngine] = useState(s && !known(s.engine) ? s.engine : "");
  const [kagi, setKagi] = useState("");
  const [service, setService] = useState(now?.provider && now.provider !== "browser" ? now.provider : "duckduckgo");
  const [endpoint, setEndpoint] = useState(service === "searxng" ? s?.searxng ?? "" : s?.custom ?? "");
  const [key, setKey] = useState("");
  const [how, setHow] = useState(sendKey(s?.send_key ?? "bearer")[0]);
  const [param, setParam] = useState(sendKey(s?.send_key ?? "bearer")[1]);
  const [calls, setCalls] = useState(String(now?.calls ?? ""));
  const keySet = !!s?.keys[mode === "browser" ? "kagi" : service];
  const needsKey = mode === "service" && service in KEY_SITES;
  const byAddress = mode === "service" && (service === "searxng" || service === "custom");
  const pickService = (v: string) => {
    setService(v);
    setKey("");
    setEndpoint(v === "searxng" ? s?.searxng ?? "" : v === "custom" ? s?.custom ?? "" : "");
  };
  const ready =
    mode === "browser"
      ? (engine !== "other" || otherEngine.trim() !== "") && (engine !== "kagi" || keySet || kagi.trim() !== "")
      : (!needsKey || keySet || key.trim() !== "") && (!byAddress || endpoint.trim() !== "");
  async function save() {
    const n = Number(calls);
    const body =
      mode === "browser"
        ? { provider: "browser", engine: engine === "other" ? otherEngine.trim() : engine, kagi: engine === "kagi" && kagi.trim() ? kagi.trim() : undefined }
        : {
            provider: service,
            key: key.trim() || undefined,
            endpoint: byAddress ? endpoint.trim() : undefined,
            send_key: service === "custom" ? (how === "bearer" ? "bearer" : `${how}:${param.trim()}`) : undefined,
          };
    const t = await run((api) =>
      api.put<ToolList>("/tools/web.search", { ...body, max_calls: Number.isInteger(n) && n > 0 && n !== now?.calls ? n : undefined }),
    );
    if (t) onDone(t);
  }
  return (
    <div className="inline-form search-form">
      <h3>{picker ? "Choose how crews search the web" : "Edit Web search"}</h3>
      {picker}
      <div className="modes" role="group" aria-label="How to search">
        <button aria-pressed={mode === "browser"} onClick={() => setMode("browser")}>
          In the browser
        </button>
        <button aria-pressed={mode === "service"} onClick={() => setMode("service")}>
          A search service
        </button>
      </div>
      {mode === "browser" ? (
        <>
          <Field
            label="Search engine"
            hint={
              engine === "google"
                ? "Google often turns away a browser that's automated, so searches may fail. Another engine is more reliable."
                : engine === "qwant"
                  ? "Qwant sometimes turns away a browser that's automated, so a search may not always work."
                  : engine === "brave"
                    ? "Brave Search sometimes turns away a browser that's automated, and it gives each result's site rather than the page."
                    : browserFound
                  ? "Crews search on the engine's own page, in the browser, as you would."
                  : "No browser is set up yet, so this won't work until one is: choose one in Settings › General."
            }
          >
            <span className="form-row tight">
              <select aria-label="Search engine" value={engine} onChange={(e) => setEngine(e.target.value)}>
                {ENGINES.map(([v, l]) => (
                  <option key={v} value={v}>
                    {v === "duckduckgo" ? `${l} (default)` : l}
                  </option>
                ))}
              </select>
            </span>
          </Field>
          {engine === "other" && (
            <Field label="Search engine's address" hint="Its home page: Ombro uses its search box. Or its results address, with %s where the words go.">
              <input className="text-input" placeholder="e.g. https://search.example.com" value={otherEngine} onChange={(e) => setOtherEngine(e.target.value)} />
            </Field>
          )}
          {engine === "kagi" && (
            <Field
              label="Your Kagi session link"
              hint={
                keySet
                  ? "A session link is kept. Leave this empty to keep it."
                  : `Kagi needs you signed in. In Kagi, open Settings › Account and copy your session link here. It's kept on ${here}.`
              }
            >
              <input className="text-input" type="password" autoComplete="off" aria-label="Your Kagi session link" value={kagi} onChange={(e) => setKagi(e.target.value)} />
            </Field>
          )}
        </>
      ) : (
        <>
          <Field
            label="Service"
            hint={service === "duckduckgo" ? "Free and needs no browser, but unofficial and rate-limited, so best effort." : undefined}
          >
            <span className="form-row tight">
              <select aria-label="Service" value={service} onChange={(e) => pickService(e.target.value)}>
                {SERVICES.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
            </span>
          </Field>
          {byAddress && (
            <Field
              label="Address"
              hint={
                service === "searxng"
                  ? "Your SearXNG's address. Its JSON output must be on: json under search: formats: in its settings.yml."
                  : "The search API's address. Put {query} where the words go, or they're sent as ?q=. Results are found in its answer by their url or link."
              }
            >
              <input
                className="text-input"
                placeholder={service === "searxng" ? "e.g. http://192.168.1.20:8888" : "e.g. https://api.example.com/search?q={query}"}
                value={endpoint}
                onChange={(e) => setEndpoint(e.target.value)}
              />
            </Field>
          )}
          {(needsKey || service === "custom") && (
            <Field
              label={service === "custom" ? "Key (optional)" : "Key"}
              hint={
                <>
                  {keySet ? "A key is kept. Leave this empty to keep it." : `It's kept on ${here} and only sent to the service.`}
                  {KEY_SITES[service] && (
                    <>
                      {" "}
                      <a href="#" onClick={(e) => { e.preventDefault(); void openLink(KEY_SITES[service]); }}>
                        Get a key
                      </a>
                    </>
                  )}
                </>
              }
            >
              <input className="text-input" type="password" autoComplete="off" aria-label="Key" value={key} onChange={(e) => setKey(e.target.value)} />
            </Field>
          )}
          {service === "custom" && (
            <Field label="How the key is sent" hint="The service's docs say which. Most use a bearer token.">
              <span className="form-row tight">
                <select aria-label="How the key is sent" value={how} onChange={(e) => setHow(e.target.value)}>
                  <option value="bearer">As a bearer token</option>
                  <option value="header">In a header</option>
                  <option value="query">In the address</option>
                </select>
                {how !== "bearer" && (
                  <input
                    aria-label={how === "header" ? "Header name" : "Name in the address"}
                    placeholder={how === "header" ? "X-API-Key" : "api_key"}
                    value={param}
                    onChange={(e) => setParam(e.target.value)}
                  />
                )}
              </span>
            </Field>
          )}
        </>
      )}
      <Field label="Calls a job may make">
        <input className="text-input number" type="number" min={1} aria-label="Calls a job may make" value={calls} onChange={(e) => setCalls(e.target.value)} />
      </Field>
      <div className="form-actions">
        <button className="primary" disabled={busy || !ready || (how !== "bearer" && service === "custom" && mode === "service" && !param.trim())} onClick={() => void save()}>
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

type Kind = "remote" | "local" | "api" | "builtin";

export const ACCESS: [string, string][] = [
  ["read-only", "No, it only looks things up"],
  ["internal", "Yes, in my own tools"],
  ["external", "Yes, and it reaches people outside or spends money"],
];
const ACCESS_SHORT: Record<string, string> = {
  "read-only": "Only looks things up",
  internal: "Can change things in your own tools",
  external: "Can change things or reach outside",
};

/** How an API's key is sent, as the form's choice and the name it's given. */
export function sendKey(value: string): [string, string] {
  if (value.startsWith("header:")) return ["header", value.slice(7)];
  if (value.startsWith("query:")) return ["query", value.slice(6)];
  return ["bearer", ""];
}

function ApiForm({ current, onDone, onCancel }: { current: ApiRow | null; onDone: (t: ToolList) => void; onCancel: () => void }) {
  const here = useStore((s) => (s.remote ? "the server" : "this computer"));
  const { run, problem, busy } = useAction();
  const [name, setName] = useState(current?.name ?? "");
  const [url, setUrl] = useState(current?.url ?? "");
  const [key, setKey] = useState("");
  const [how, setHow] = useState(sendKey(current?.send_key ?? "bearer")[0]);
  const [param, setParam] = useState(sendKey(current?.send_key ?? "bearer")[1]);
  const [about, setAbout] = useState(current?.about ?? "");
  const [howTo, setHowTo] = useState(current?.how_to ?? "");
  const [access, setAccess] = useState(current?.access ?? "external");
  const [getsLookUp, setGetsLookUp] = useState(current?.get_looks_up ?? true);
  const [calls, setCalls] = useState(String(current?.calls ?? 30));
  const plain = url.trim().startsWith("http://");
  async function save() {
    const n = Number(calls);
    const body = {
      name, url: url.trim(), key: key || undefined, send_key: how, key_param: how === "bearer" ? undefined : param.trim(),
      about, how_to: howTo, access, get_looks_up: access === "read-only" ? undefined : getsLookUp, max_calls: Number.isInteger(n) && n > 0 ? n : undefined,
    };
    const t = current
      ? await run((api) => api.put<ToolList>(`/tools/apis/${current.name}`, body))
      : await run((api) => api.post<ToolList>("/tools/apis", body));
    if (t) onDone(t);
  }
  return (
    <div className="inline-form">
      <h3>{current ? `Edit ${tidy(current.name)}` : "Add a web API"}</h3>
      {!current && (
        <Field label="Name" hint="A short name crews will know it by, such as podcasts.">
          <input className="text-input short" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
      )}
      <Field
        label="Address"
        hint={plain ? "This address isn't https, so the key travels unencrypted. Fine on your own network; take care elsewhere." : "The API's base address, from its docs. Crews can only reach addresses under it."}
      >
        <input className="text-input" placeholder="e.g. https://api.podcastlist.com/v2" value={url} onChange={(e) => setUrl(e.target.value)} />
      </Field>
      <Field
        label="Key"
        hint={current?.key_set ? "A key is kept. Leave this empty to keep it." : `Kept on ${here} and only sent to this address. Leave empty if it needs none.`}
      >
        <input className="text-input" type="password" autoComplete="off" aria-label="Key" value={key} onChange={(e) => setKey(e.target.value)} />
      </Field>
      <Field label="How the key is sent" hint="The API's docs say which. Most use a bearer token.">
        <span className="form-row tight">
          <select aria-label="How the key is sent" value={how} onChange={(e) => setHow(e.target.value)}>
            <option value="bearer">As a bearer token</option>
            <option value="header">In a header</option>
            <option value="query">In the address</option>
          </select>
          {how !== "bearer" && (
            <input
              aria-label={how === "header" ? "Header name" : "Name in the address"}
              placeholder={how === "header" ? "X-API-Key" : "api_key"}
              value={param}
              onChange={(e) => setParam(e.target.value)}
            />
          )}
        </span>
      </Field>
      <Field label="What it's for" hint="A line or two. The Lead decides from this when the API helps a brief.">
        <input className="text-input" value={about} onChange={(e) => setAbout(e.target.value)} />
      </Field>
      <Field label="How to use it (optional)" hint="Its paths and what they return, pasted from its docs. For example: GET /episodes?q=… searches episodes by title.">
        <textarea className="text-area" rows={5} value={howTo} maxLength={2000} onChange={(e) => setHowTo(e.target.value)} />
      </Field>
      <Field label="Can it change things?" hint="Lookups always run. Changes follow your approval mode, so some wait in Approvals.">
        <span className="form-row tight">
          <select aria-label="Can it change things?" value={access} onChange={(e) => setAccess(e.target.value)}>
            {ACCESS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </span>
      </Field>
      {access !== "read-only" && (
        <Field
          label="Do GET requests to this API only look things up?"
          hint="Most APIs only look things up with GET. Some send or change things with it, such as a bot's sendMessage or a webhook: then say no, and its GETs follow your answer above."
        >
          <span className="form-row tight">
            <select
              aria-label="Do GET requests to this API only look things up?"
              value={getsLookUp ? "yes" : "no"}
              onChange={(e) => setGetsLookUp(e.target.value === "yes")}
            >
              <option value="yes">Yes, they only look things up</option>
              <option value="no">No, they can send or change things</option>
            </select>
          </span>
        </Field>
      )}
      <Field label="Calls a job may make">
        <input className="text-input number" type="number" min={1} aria-label="Calls a job may make" value={calls} onChange={(e) => setCalls(e.target.value)} />
      </Field>
      <div className="form-actions">
        <button className="primary" disabled={busy || (!current && !name.trim()) || !url.trim()} onClick={() => void save()}>
          {current ? "Save" : "Add"}
        </button>
        <button className="secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
      <Problem text={problem} />
    </div>
  );
}
/** Connect a Composio account by its project API key, or change the key. Composio's tools then show
 * in the list, one row for each service connected there. */
function ComposioForm({ current, onDone, onCancel }: { current: ComposioAccount | null; onDone: (t: ToolList) => void; onCancel: () => void }) {
  const here = useStore((s) => (s.remote ? "the server" : "this computer"));
  const { run, problem, busy } = useAction();
  const [key, setKey] = useState("");
  async function save() {
    const t = await run((api) => api.put<ToolList>("/tools/composio", { key: key.trim() }));
    if (t) onDone(t);
  }
  return (
    <div className="inline-form">
      <h3>{current ? "Change your Composio key" : "Connect my Composio account"}</h3>
      <p className="muted">
        Composio connects services such as Google Docs, Gmail and Sheets for you. In the Composio dashboard, choose{" "}
        <strong>Platform</strong> and your project, connect the services there, and copy the project's key (it starts{" "}
        <code>ak_</code>) from{" "}
        <a href="#" onClick={(e) => { e.preventDefault(); void openLink("https://dashboard.composio.dev/~/project/settings/api-keys"); }}>Settings › API Keys</a>.
        The <code>ck_</code> key under For You won't work here. Use and limits are on your Composio account.
      </p>
      <Field label="Composio API key" hint={`Kept on ${here}, and only ever sent to Composio. It's checked with Composio before it's kept.`}>
        <input className="text-input" type="password" autoComplete="off" aria-label="Composio API key" value={key} onChange={(e) => setKey(e.target.value)} />
      </Field>
      <div className="form-actions">
        <button className="primary" disabled={busy || !key.trim()} onClick={() => void save()}>
          {busy ? "Checking…" : current ? "Save" : "Connect"}
        </button>
        <button className="secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
      <Problem text={problem} />
    </div>
  );
}

type Secret = { name: string; value: string; kept: boolean };

/** How a server's sign-in reads, and whether it needs one now. `remote`: Ombro is on a server, so a
 * tool started by a command runs there. */
export function signInText(s: ServerRow, remote = false): [string, boolean] {
  if (s.signing_in) return ["Waiting for you to sign in, in your browser…", false];
  if (s.problem) return [s.problem, true];
  if (!s.url) return [remote ? "On the server" : "On this computer", false];
  const st = s.sign_in ?? "";
  if (st === "not signed in") return ["Not signed in", true];
  if (st === "sign-in expired") return ["Sign-in expired", true];
  return [st.charAt(0).toUpperCase() + st.slice(1), false];
}

function where(s: ServerRow): string {
  return s.url ?? [s.command, ...s.args].join(" ");
}

function Secrets({ rows, setRows, remote }: { rows: Secret[]; setRows: (r: Secret[]) => void; remote: boolean }) {
  const here = useStore((s) => (s.remote ? "the server" : "this computer"));
  const what = remote ? "header" : "setting";
  return (
    <div className="secrets">
      <span className="field-label">{remote ? "Secret headers (optional)" : "Secret settings (optional)"}</span>
      <span className="field-hint">
        {remote
          ? "If the service gives you a key, add it here, for example Authorization and the key. It's kept on " + here + "."
          : "If the program needs a key, add it here, for example API_KEY and the key. It's kept on " + here + "."}
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
  onDone: (t: ToolList, signIn: string | null, warning: string | null, added: string | null) => void;
  onCancel: () => void;
}) {
  const { run, problem, busy } = useAction();
  const [name, setName] = useState(current ? current.server : "");
  const [url, setUrl] = useState(current?.url ?? "");
  const [command, setCommand] = useState(current && !current.url ? where(current) : "");
  const [secrets, setSecrets] = useState<Secret[]>(current ? current.secrets.map((n) => ({ name: n, value: "", kept: true })) : []);
  const remote = kind === "remote";
  const onServer = useStore((s) => s.remote);

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
    onDone(t, remote && !current ? added : null, t.warning ?? null, current ? null : added);
  }

  return (
    <div className="inline-form">
      <h3>{current ? `Edit ${tidy(current.server)}` : remote ? "Add an online service" : onServer ? "Add a program on the server" : "Add a program on this computer"}</h3>
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

type Claim = { tool: string; description: string; claims: string | null; class: string; class_from: string };
type Claims = { tools: Claim[]; unconfirmed: string[]; problem: string | null };

/** What a server says each of its tools does. Its word alone never makes a tool internal: one
 * it says stays inside your organisation counts as reaching outside, so each use waits for your
 * approval, until you confirm it here, once. Starts the server, so it may take a moment. */
export function ClaimsPanel({ server, onClose }: { server: string; onClose: () => void }) {
  const { run, problem, busy } = useAction();
  const [claims, setClaims] = useState<Claims | null>(null);
  useEffect(() => {
    void run((api) => api.get<Claims>(`/tools/servers/${server}/claims`)).then((c) => c && setClaims(c));
  }, [run, server]);
  async function confirm() {
    const c = await run((api) => api.post<Claims>(`/tools/servers/${server}/claims`, { tools: claims?.unconfirmed ?? [] }));
    if (c) setClaims(c);
  }
  const waiting = claims ? claims.tools.filter((t) => claims.unconfirmed.includes(t.tool)) : [];
  return (
    <div className="inline-form claims">
      <h3>What {tidy(server)}'s tools say they do</h3>
      {!claims && !problem && <p className="field-hint">Asking {tidy(server)} for its tools…</p>}
      {claims?.problem && <p className="field-hint">{claims.problem}. If it needs signing in, sign in, then check again.</p>}
      {claims && !claims.problem && waiting.length === 0 && (
        <p className="field-hint">Nothing to confirm: each tool is classed by what it says it does and your settings.</p>
      )}
      {waiting.length > 0 && (
        <>
          <p>
            {waiting.length === 1 ? "This tool says" : `These ${waiting.length} tools say`} they change things but stay inside your
            organisation, reaching no one outside:
          </p>
          <ul className="line-items">
            {waiting.map((t) => (
              <li key={t.tool}>
                <b>{t.tool}</b>
                {t.description ? `: ${t.description}` : ""}
              </li>
            ))}
          </ul>
          <p className="field-hint">
            Ombro can't check what a server says about itself. Until you confirm it, these count as reaching outside: each use
            waits for your approval. Once confirmed, they run without asking, as changes in your own tools do (deleting or
            sharing still asks once a job has read outside material).
          </p>
        </>
      )}
      <div className="form-actions">
        {waiting.length > 0 && (
          <button className="primary" disabled={busy} onClick={() => void confirm()}>
            Confirm they stay inside
          </button>
        )}
        <button className="secondary" onClick={onClose}>
          {waiting.length > 0 ? "Keep asking each time" : "Close"}
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
  const here = useStore((st) => (st.remote ? "the server" : "this computer"));
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
  const picker = !tool && (
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
  );
  if (which === "web.search") return <SearchForm key={which} now={now} picker={picker} browserFound={!!tools.browser} onDone={onDone} onCancel={onCancel} />;
  return (
    <div className="inline-form">
      <h3>{tool ? `Edit ${NATIVE[tool.tool]}` : "Choose the service crews use to search and read the web"}</h3>
      {picker}
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
        <Field label="Key" hint={keySet ? "A key is set. Leave this empty to keep it." : `It's kept on ${here} and only sent to the provider.`}>
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
  const [composio, setComposio] = useState(false); // the Composio form is open
  const [kind, setKind] = useState<Kind>("remote");
  const [warning, setWarning] = useState<string | null>(null);
  const remote = useStore((st) => st.remote);
  const [pasting, setPasting] = useState<string | null>(null); // a server whose sign-in answer may be pasted
  const [claims, setClaims] = useState<string | null>(null); // a server whose tools' claims are shown
  const [pasted, setPasted] = useState("");
  const t = tools.data;
  const waiting = !!t?.servers.some((s) => s.signing_in);

  // While a sign-in waits for the browser, look again every two seconds.
  useEffect(() => {
    if (!waiting) return;
    const timer = setInterval(() => void tools.reload(), 2000);
    return () => clearInterval(timer);
  }, [waiting, tools]);

  async function signIn(server: string) {
    if (!remote) {
      const r = await run((api) => api.post<{ url: string | null; state?: string; problem?: string }>(`/tools/servers/${server}/sign-in`));
      if (r?.url) await openLink(r.url);
      await tools.reload();
      return;
    }
    // Ombro is on a server, but the browser is here: the tool sends it back to this computer, where
    // the app catches the answer and hands it to the server. In a browser, paste the address instead.
    let port: number | null = null;
    if (inTauri) {
      const { invoke } = await import("@tauri-apps/api/core");
      port = await invoke<number>("sign_in_listener");
    }
    const r = await run((api) =>
      api.post<{ url: string | null; state?: string; problem?: string }>(`/tools/servers/${server}/sign-in`, {
        hand_over: true,
        redirect_port: port,
      }),
    );
    if (!r?.url) {
      await tools.reload();
      return;
    }
    setPasting(server);
    setPasted("");
    await openLink(r.url);
    await tools.reload();
    if (port !== null) {
      // Only an answer that brings back this sign-in's state is handed over.
      const answer = await catchSignIn(port, r.url, r.state);
      if (answer) await handOver(server, answer);
    }
  }

  async function handOver(server: string, url: string) {
    const ok = await run((api) => api.post(`/tools/servers/${server}/sign-in/answer`, { url }));
    if (ok) setPasting(null);
    await tools.reload();
  }

  return (
    <>
      <h1 className="page-title">Connected tools</h1>
      <p className="lede">
        The MCP servers, APIs and Composio services your crews can use on their jobs. These are currently offered to every crew member.
      </p>
      <Problem text={tools.problem} />
      {t && (
        <div className="rows">
          {t.native
            .filter((n) => n.tool in NATIVE)
            .map((n) => (
              <div className="row-block" key={n.tool}>
                <Item
                  name={NATIVE[n.tool]}
                  details={[
                    `Built in, using ${n.tool === "web.search" ? searchUsing(n) : (PROVIDERS[n.provider ?? ""] ?? n.provider ?? "").replace(/ \(.*\)$/, "")}`,
                    n.calls ? `Up to ${n.calls} calls a job` : null,
                  ]}
                  actions={
                    <button className="text-button strong" onClick={() => { setEditing(n.tool); setAdding(null); }}>
                      Edit
                    </button>
                  }
                />
                {editing === n.tool && (
                  <BuiltinForm tool={n} tools={t} onCancel={() => setEditing(null)} onDone={(x) => { tools.set(x); setEditing(null); }} />
                )}
              </div>
            ))}
          {t.apis.map((a) => (
            <div className="row-block" key={`api:${a.name}`}>
              <Item
                name={tidy(a.name)}
                details={[
                  <span title={a.url}>{a.url}</span>,
                  ACCESS_SHORT[a.access] ?? a.access,
                  a.key && !a.key_set ? <span className="problem-inline">No key yet</span> : null,
                  `Up to ${a.calls} calls a job`,
                ]}
                actions={
                  <>
                    <button className="text-button strong" onClick={() => { setEditing(`api:${a.name}`); setAdding(null); }}>
                      Edit
                    </button>
                    <button
                      className="text-button strong"
                      onClick={() => void removal.ask(`api:${a.name}`, () => useStore.getState().api!.get<Check>(`/tools/apis/${a.name}/check`))}
                    >
                      Delete
                    </button>
                  </>
                }
              />
              {editing === `api:${a.name}` && (
                <ApiForm current={a} onCancel={() => setEditing(null)} onDone={(x) => { tools.set(x); setEditing(null); }} />
              )}
              {removal.target === `api:${a.name}` && (
                <RemovePanel
                  removal={removal}
                  what={tidy(a.name)}
                  onConfirm={async () => {
                    const x = await run((api) => api.del<ToolList>(`/tools/apis/${a.name}`));
                    if (x) tools.set(x);
                    return !!x;
                  }}
                />
              )}
            </div>
          ))}
          {t.composio?.toolkits.map((k) => (
            <div className="row-block" key={`composio:${k.slug}`}>
              <Item
                name={`${k.title} (via Composio)`}
                details={[
                  k.active ? (k.tools ? `${k.tools} tools` : "Connected") : <span className="problem-inline">Connection {k.status}: reconnect it in Composio</span>,
                  k.active ? ACCESS_SHORT[t.composio!.access] ?? t.composio!.access : null,
                ]}
                actions={null}
              />
            </div>
          ))}
          {t.servers.map((s) => {
            const [state, needs] = signInText(s, remote);
            return (
              <div className="row-block" key={s.server}>
                <Item
                  name={tidy(s.server)}
                  details={[
                    <span title={where(s)}>{shortPath(where(s))}</span>,
                    <span className={s.problem ? "problem-inline" : ""}>{state}</span>,
                  ]}
                  actions={
                    <>
                      {s.url && needs && !s.signing_in && (
                        <button className="text-button strong" onClick={() => void signIn(s.server)}>
                          Sign in
                        </button>
                      )}
                      <button className="text-button strong" onClick={() => setClaims(s.server)}>
                        Check its tools
                      </button>
                      <button className="text-button strong" onClick={() => { setEditing(s.server); setAdding(null); }}>
                        Edit
                      </button>
                      <button
                        className="text-button strong"
                        onClick={() => void removal.ask(s.server, () => useStore.getState().api!.get<Check>(`/tools/servers/${s.server}/check`))}
                      >
                        Delete
                      </button>
                    </>
                  }
                />
                {editing === s.server && (
                  <ServerForm
                    current={s}
                    kind={s.url ? "remote" : "local"}
                    onCancel={() => setEditing(null)}
                    onDone={(x, _s, w) => { tools.set(x); setEditing(null); setWarning(w); }}
                  />
                )}
                {claims === s.server && <ClaimsPanel server={s.server} onClose={() => setClaims(null)} />}
                {pasting === s.server && s.signing_in && (
                  <details className="more paste-answer">
                    <summary>Signed in, but Ombro is still waiting?</summary>
                    <Field label="Paste the address your browser ended up on" hint="Copy it from the browser's address bar after signing in.">
                      <span className="form-row">
                        <input
                          className="text-input"
                          aria-label="The address your browser ended up on"
                          value={pasted}
                          onChange={(e) => setPasted(e.target.value)}
                        />
                        <button className="secondary" disabled={!pasted.trim()} onClick={() => void handOver(s.server, pasted)}>
                          Use it
                        </button>
                      </span>
                    </Field>
                  </details>
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
      {composio && !t?.composio && (
        <ComposioForm current={null} onCancel={() => setComposio(false)} onDone={(x) => { tools.set(x); setComposio(false); }} />
      )}
      {adding === null ? (
        !(composio && !t?.composio) && (
          <div className="form-actions">
            <button className="primary" onClick={() => { setAdding(kind); setEditing(null); setComposio(false); }}>
              Add a tool
            </button>
            {t && !t.composio && (
              <button className="secondary" onClick={() => { setComposio(true); setAdding(null); setEditing(null); }}>
                Connect my Composio account
              </button>
            )}
          </div>
        )
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
                <option value="remote">MCP - Online service</option>
                <option value="local">{remote ? "MCP - On the server" : "MCP - On this computer"}</option>
                <option value="api">API - Any web service</option>
                <option value="builtin">Built in - Web search and reading pages</option>
              </select>
            </span>
          </Field>
          {adding === "api" ? (
            <ApiForm current={null} onCancel={() => setAdding(null)} onDone={(x) => { tools.set(x); setAdding(null); }} />
          ) : adding === "builtin" && t ? (
            <BuiltinForm tool={null} tools={t} onCancel={() => setAdding(null)} onDone={(x) => { tools.set(x); setAdding(null); }} />
          ) : (
            <ServerForm
              key={adding}
              current={null}
              kind={adding === "local" ? "local" : "remote"}
              onCancel={() => setAdding(null)}
              onDone={(x, toSignIn, w, added) => {
                tools.set(x);
                setAdding(null);
                setWarning(w);
                if (toSignIn) void signIn(toSignIn);
                // A program on this computer can be asked for its tools now: what they claim, to confirm.
                else if (added) setClaims(added);
              }}
            />
          )}
        </div>
      )}
      {t?.composio && (
        <div className="composio-account">
          <h2>Composio</h2>
          <div className="rows">
            <div className="row-block">
              <Item
                name="Composio account"
                details={[
                  t.composio.problem ? (
                    <span className="problem-inline">{t.composio.problem}</span>
                  ) : t.composio.toolkits.length ? (
                    `${t.composio.toolkits.length} service${t.composio.toolkits.length === 1 ? "" : "s"} connected`
                  ) : (
                    "No services connected in Composio yet"
                  ),
                  <a href="#" onClick={(e) => { e.preventDefault(); void openLink(t.composio!.dashboard); }}>Connect more in Composio</a>,
                ]}
                actions={
                  <>
                    <button className="text-button strong" onClick={async () => { const x = await run((api) => api.post<ToolList>("/tools/composio/refresh")); if (x) tools.set(x); }}>
                      Refresh
                    </button>
                    <button className="text-button strong" onClick={() => { setComposio(true); setAdding(null); setEditing(null); }}>
                      Change key
                    </button>
                    <button className="text-button strong" onClick={() => void removal.ask("composio", () => useStore.getState().api!.get<Check>("/tools/composio/check"))}>
                      Disconnect
                    </button>
                  </>
                }
              />
              {composio && (
                <ComposioForm current={t.composio} onCancel={() => setComposio(false)} onDone={(x) => { tools.set(x); setComposio(false); }} />
              )}
              {removal.target === "composio" && (
                <RemovePanel
                  removal={removal}
                  what="your Composio account"
                  onConfirm={async () => {
                    const x = await run((api) => api.del<ToolList>("/tools/composio"));
                    if (x) tools.set(x);
                    return !!x;
                  }}
                />
              )}
            </div>
          </div>
        </div>
      )}
      {warning && <p className="warning-note">Note: {warning}</p>}
      <Problem text={problem} />
    </>
  );
}
