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
  if (parts[0] === "mcp" && parts.length >= 3) return parts.slice(1, -1).map((p) => capital(words(p))).join(" ");
  return "";
}

/** What a tool does, readably: `mcp.sheets.post_message` → "Post message in Sheets". */
export function toolAction(tool: string): string {
  if (tool === SKILL_TOOL) return "Add or change a skill";
  const action = capital(words(tool.split(".").slice(-1)[0] || tool));
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

/** The row's title: what it will do, and its gist. */
export function approvalTitle(tool: string, args: unknown): string {
  const g = gist(args);
  return g ? `${toolAction(tool)}: “${g}”` : toolAction(tool);
}

/** The arguments as labelled lines: [["Channel", "news"], ["Text", "…"]]. */
export function argumentLines(args: unknown): [string, string][] {
  if (args === null || args === undefined) return [];
  if (typeof args !== "object" || Array.isArray(args)) return [["Value", plain(args)]];
  return Object.entries(args as Record<string, unknown>).map(([k, v]) => [capital(words(k)), plain(v)]);
}
