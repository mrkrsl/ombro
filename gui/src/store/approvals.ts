// An approval in words. A staged action is a tool call: a name like `mcp.sheets.post_message` and
// its arguments. People shouldn't need to read either, so the window says what it will do
// ("Post message in Sheets"), quotes the gist ("Reservoir news, all invented."), and lists the
// arguments as labelled lines instead of JSON.

const SKILL_TOOL = "skills.propose";

/** Words from a name: `post_message` → "post message", `sendEmail` → "send email". */
function words(name: string): string {
  return name
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_\-.]+/g, " ")
    .trim()
    .toLowerCase();
}

function capital(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Where a tool lives, readably: `mcp.sheets.post_message` → "Sheets"; built-ins have none. */
export function toolPlace(tool: string): string {
  const parts = tool.split(".");
  if ((parts[0] === "mcp" || parts[0] === "api") && parts.length >= 3) return parts.slice(1, -1).map((p) => capital(words(p))).join(" ");
  if (parts[0] === "composio" && parts.length >= 3) return `${capital(words(parts[1]))} (via Composio)`;
  return "";
}

/** What a tool does, readably: `mcp.sheets.post_message` → "Post message in Sheets". */
export function toolAction(tool: string): string {
  if (tool === SKILL_TOOL) return "Add or change a skill";
  if (tool === "browser.submit") return "Submit a form on a website";
  const last = tool.split(".").slice(-1)[0] || tool;
  // Composio's tool names are in capitals: GOOGLEDOCS_CREATE_DOCUMENT reads as "Create document".
  const action = capital(tool.startsWith("composio.") ? words(last).toLowerCase() : words(last));
  const place = toolPlace(tool);
  return place ? `${action} in ${place}` : action;
}

// The argument most likely to say what the action is about, in this order.
const GIST = ["subject", "title", "name", "text", "message", "body", "content", "summary", "query"];

function plain(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return v.map(plain).join(", ");
  return Object.entries(v as Record<string, unknown>)
    .map(([k, x]) => `${capital(words(k))}: ${plain(x)}`)
    .join("; ");
}

/** The short quote that says what this one is about, if an argument holds one. */
export function gist(args: unknown, n = 80): string {
  if (!args || typeof args !== "object" || Array.isArray(args)) return "";
  const a = args as Record<string, unknown>;
  const key = GIST.find((k) => typeof a[k] === "string" && (a[k] as string).trim());
  if (!key) return "";
  const text = (a[key] as string).replace(/\s+/g, " ").trim();
  return text.length <= n ? text : text.slice(0, n - 1) + "…";
}

// A web API request in words, by its method.
const METHODS: Record<string, string> = { GET: "Look up", POST: "Send to", PUT: "Replace", PATCH: "Change", DELETE: "Delete" };

/** The row's title: what it will do, and its gist. */
type StagedForm = { url?: string; fields?: { field?: string; selector?: string; value?: string }[]; button?: { label?: string } };

function host(url: string | undefined): string {
  try {
    return new URL(url ?? "").host;
  } catch {
    return url ?? "";
  }
}

export function approvalTitle(tool: string, args: unknown): string {
  if (tool === "browser.submit" && args && typeof args === "object") {
    const a = args as StagedForm;
    const button = (a.button?.label ?? "").trim();
    return `Submit a form on ${host(a.url)}${button ? `: “${button}”` : ""}`;
  }
  if (tool.startsWith("api.") && args && typeof args === "object") {
    const a = args as Record<string, unknown>;
    const method = String(a.method ?? (tool.endsWith(".get") ? "GET" : "POST")).toUpperCase();
    const g = gist(a.body);
    const what = `${METHODS[method] ?? method} ${String(a.path ?? "")} in ${toolPlace(tool)}`;
    return g ? `${what}: “${g}”` : what;
  }
  const g = gist(args);
  return g ? `${toolAction(tool)}: “${g}”` : toolAction(tool);
}

/** The arguments as labelled lines: [["Channel", "news"], ["Text", "…"]]. */
export function argumentLines(args: unknown, tool = ""): [string, string][] {
  if (args === null || args === undefined) return [];
  if (tool === "browser.submit" && typeof args === "object" && !Array.isArray(args)) {
    const a = args as StagedForm;
    return [
      ["Page", a.url ?? ""],
      ...(a.fields ?? []).map((f): [string, string] => [f.field || f.selector || "Field", f.value ?? ""]),
      ["Button", a.button?.label ?? ""],
    ];
  }
  if (typeof args !== "object" || Array.isArray(args)) return [["Value", plain(args)]];
  return Object.entries(args as Record<string, unknown>).map(([k, v]) => [capital(words(k)), plain(v)]);
}
