// The Settings pages against a fake serve: what each shows, and what it sends. All data is invented.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Client } from "../../api/serve";
import type { Crews as CrewsView, FolderList, ModelList, Preferences, ServerRow, ToolList } from "../../api/types";
import { screenFor, useStore } from "../../store/store";
import { crewName } from "./Crews";
import { Settings } from "./Settings";
import { usedBy, type Library } from "./Skills";
import { approvalTitle } from "../../store/approvals";
import { sendKey, signInText } from "./Tools";
import { shortPath } from "./common";

const readSkillFolder = vi.hoisted(() => vi.fn());
vi.mock("../../platform", async (original) => ({ ...(await original<typeof import("../../platform")>()), readSkillFolder }));

type Call = [string, string, unknown?];

const prefs: Preferences = {
  name: "Ada", theme: "auto", default_crew: "default", default_crew_title: "Default Crew", editor: "",
  outward: "balanced", instructions_set: true, instructions: "I run an invented bakery.", background_feed: true,
  notifications: true, thinking: "medium", thinking_set: false, files: {},
};
const models: ModelList = {
  default: "invented-chat",
  connections: [
    { api: "openai", label: "Invented", endpoint: "https://api.invented.invalid", key: "INVENTED_API_KEY",
      models: [{ name: "invented-chat", model: "invented-chat", default: true, used_by: [] }] },
    { api: "openai", label: "Local", endpoint: "http://192.168.1.100:1234/v1", key: null,
      models: [{ name: "local-model", model: "local-model", default: false, used_by: [] }] },
  ],
  presets: [
    { label: "Invented", endpoint: "https://api.invented.invalid", key: "INVENTED_API_KEY", model: null },
    { label: "Anthropic", endpoint: "https://api.anthropic.com/v1", key: "ANTHROPIC_API_KEY", model: "claude-opus-5-5" },
    { label: "Ollama, on this computer", endpoint: "http://localhost:11434/v1", key: "", model: null },
  ],
  chatgpt: { state: "not signed in", email: null, signing_in: false, problem: null, welcome: null,
    usage_url: "https://chatgpt.com/settings/usage" },
};
const crews: CrewsView = {
  lead: { name: "lead", title: "Lead", model: "default", path: "/x" },
  default_crew: "default",
  crews: [
    { name: "legal", title: "Legal Crew", description: "", default: false, roles: [
      { name: "counsel", title: "Legal Researcher", kind: "specialist", model: "default" },
      { name: "assistant", title: "Legal Assistant", kind: "helper", model: "local-model" },
    ] },
  ],
};
const folders: FolderList = {
  folders: [{ name: "big-inc", path: "/Users/ada/Projects/big-inc", access: "write", backup: null, problem: null }],
  declined: [], file: "/x",
};
const remote: ServerRow = { server: "notion", url: "https://mcp.invented.invalid", command: null, args: [], secrets: [],
  sign_in: "not signed in", signing_in: false, problem: null };
const tools: ToolList = {
  native: [{ tool: "web.search", provider: "duckduckgo", max_calls: null, calls: 60 },
    { tool: "web.fetch", provider: "browser", max_calls: null, calls: 100 }],
  servers: [remote], sandbox: null, folders: [], composio: null, browser: "Google Chrome",
  apis: [{ name: "podcasts", url: "https://api.podcasts.invalid/v2", about: "Invented list.", how_to: "",
    access: "read-only", get_looks_up: true, send_key: "bearer", key: "PODCASTS_API_KEY", key_set: true, calls: 30 }],
};
const library: Library = {
  skills: [{ name: "fact-check", description: "Checks facts.", source: "you", path: "/x", used_by: ["legal/counsel"], warning: null }],
  all_skills: [], waiting: [],
};

let calls: Call[] = [];
let replies: Record<string, unknown> = {};

function fakeApi(): Client {
  const answer = (method: string) => async (path: string, body?: unknown) => {
    calls.push([method, path, body]);
    const r = replies[`${method} ${path}`] ?? replies[path];
    if (r instanceof Error) throw r;
    return (r ?? {}) as never;
  };
  return { get: answer("GET"), post: answer("POST"), put: answer("PUT"), del: answer("DELETE") } as unknown as Client;
}

function sent(method: string, path: string): unknown {
  return calls.find(([m, p]) => m === method && p === path)?.[2];
}

const browser = { choice: "auto", using: "Google Chrome", endpoint: null, problem: null,
  installed: [{ name: "Google Chrome", path: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" }] };

beforeEach(() => {
  calls = [];
  replies = { "/preferences": prefs, "/models": models, "/limits": { allowance: "moderate" }, "/crews": crews,
    "/folders": folders, "/tools": tools, "/skills": library, "/keys": { keys: [], sign_ins: [], file: "/x" },
    "/browser": browser };
  useStore.setState({ api: fakeApi(), settingsPage: "general", screen: "settings", remote: false });
});

describe("the settings rules", () => {
  it("opens a command's page", () => {
    expect(screenFor("settings/crews")).toEqual({ screen: "settings", page: "crews" });
    expect(screenFor("settings")).toEqual({ screen: "settings", page: undefined });
    expect(screenFor("jobs")).toEqual({ screen: "jobs" });
  });

  it("names things for people", () => {
    expect(crewName("Legal Team")).toBe("legal-team");
    expect(crewName("Marketing Crew")).toBe("marketing");
    expect(shortPath("/Users/ada/Projects/work")).toBe("~/Projects/work");
    expect(signInText(remote)).toEqual(["Not signed in", true]);
    expect(signInText({ ...remote, sign_in: "signed in (renews itself)" })).toEqual(["Signed in (renews itself)", false]);
    expect(signInText({ ...remote, url: null, command: "uvx x==1" })).toEqual(["On this computer", false]);
    expect(signInText({ ...remote, url: null, command: "uvx x==1" }, true)).toEqual(["On the server", false]);
    expect(sendKey("header:X-API-Key")).toEqual(["header", "X-API-Key"]);
    expect(approvalTitle("api.podcasts.request", { method: "POST", path: "/playlists", body: { title: "Invented list" } }))
      .toBe("Send to /playlists in Podcasts: “Invented list”");
    expect(approvalTitle("composio.googledocs.CREATE_DOCUMENT", { title: "Invented notes" }))
      .toBe("Create document in Googledocs (via Composio): “Invented notes”");
    expect(usedBy(library.skills[0], { ...library, all_skills: ["legal/assistant", "ops/clerk"] }, crews)).toBe("Legal Crew, Ops");
  });
});

describe("Settings › General", () => {
  it("saves your name and profile together", async () => {
    replies["PUT /preferences"] = { ...prefs, name: "Ada L" };
    render(<Settings />);
    const name = await screen.findByLabelText("What should Ombro call you?");
    expect(screen.getByLabelText(/Your profile/)).toHaveValue("I run an invented bakery.");
    await userEvent.type(name, " L");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(sent("PUT", "/preferences")).toEqual({ name: "Ada L", instructions: "I run an invented bakery." });
  });

  it("names each model's provider in the default model list", async () => {
    render(<Settings />);
    const choice = await screen.findByLabelText("Default model");
    expect(within(choice).getAllByRole("option").map((o) => o.textContent)).toEqual(["invented-chat (Invented)", "local-model (Local)"]);
  });

  it("lists providers and says why one can't go", async () => {
    replies["POST /models/providers/check"] = { blocked: "Your default model comes from this provider.", warnings: [] };
    render(<Settings />);
    expect(await screen.findByText("Invented")).toBeInTheDocument();
    expect(screen.getByText("http://192.168.1.100:1234/v1")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]);
    expect(await screen.findByRole("alert")).toHaveTextContent("Your default model comes from this provider.");
    expect(sent("POST", "/models/providers/check")).toEqual({ endpoint: "https://api.invented.invalid" });
    expect(calls.some(([, p]) => p === "/models/providers/remove")).toBe(false);
  });

  it("sends a new provider's key once, and the approval mode", async () => {
    replies["POST /models/providers"] = models;
    render(<Settings />);
    await userEvent.click(await screen.findByRole("button", { name: "Add provider" }));
    await userEvent.selectOptions(screen.getByLabelText("Provider"), "other");
    await userEvent.type(screen.getByLabelText(/^Address/), "https://api.other.invalid");
    await userEvent.type(screen.getByLabelText(/^API key/), "sk-INVENTED");
    await userEvent.click(screen.getAllByRole("button", { name: "Add provider" })[0]);
    expect(sent("POST", "/models/providers")).toEqual({ endpoint: "https://api.other.invalid", key: "sk-INVENTED", model: "" });
    replies["PUT /preferences"] = { ...prefs, outward: "manual" };
    await userEvent.selectOptions(screen.getByLabelText("Approval mode"), "manual");
    expect(sent("PUT", "/preferences")).toEqual({ outward: "manual" });
  });
});

describe("Settings › General: thinking", () => {
  it("offers the five levels and saves the choice as a preference", async () => {
    render(<Settings />);
    const picker = await screen.findByLabelText("Thinking");
    expect([...(picker as HTMLSelectElement).options].map((o) => o.text))
      .toEqual(["No thinking", "Low", "Medium", "High", "Extra high"]);
    expect((picker as HTMLSelectElement).value).toBe("medium");
    replies["PUT /preferences"] = { ...prefs, thinking: "low", thinking_set: true };
    await userEvent.selectOptions(picker, "low");
    expect(sent("PUT", "/preferences")).toEqual({ thinking: "low" });
  });
});

describe("Settings › General: the browser", () => {
  it("is one choice: automatic, a browser on this computer, or none", async () => {
    render(<Settings />);
    const picker = await screen.findByLabelText("Browser");
    expect([...(picker as HTMLSelectElement).options].map((o) => o.text))
      .toEqual(["Automatic (Google Chrome)", "Google Chrome", "Don't use a browser"]);
    replies["PUT /browser"] = { ...browser, choice: "off", using: null,
      problem: "Turned off: crews and chat can't read web pages (search still works)." };
    await userEvent.selectOptions(picker, "off");
    expect(sent("PUT", "/browser")).toEqual({ choice: "off" });
    expect(await screen.findByText(/Turned off/)).toBeTruthy();
  });
});

describe("Settings › General: the ChatGPT plan", () => {
  it("is a provider to choose, next to an OpenAI API key, and signs in in the browser", async () => {
    replies["/models"] = { ...models, presets: [
      { label: "OpenAI API (key)", endpoint: "https://api.openai.com/v1", key: "OPENAI_API_KEY", model: null }, ...models.presets] };
    replies["POST /models/chatgpt/sign-in"] = { url: "https://auth.openai.com/api/accounts/authorize?x=1", state: "waiting" };
    render(<Settings />);
    await userEvent.click(await screen.findByRole("button", { name: "Add provider" }));
    const picker = screen.getByLabelText("Provider");
    const options = [...(picker as HTMLSelectElement).options].map((o) => o.text);
    expect(options.slice(0, 2)).toEqual(["OpenAI API (key)", "ChatGPT plan (sign in, no key)"]);
    await userEvent.selectOptions(picker, "chatgpt");
    expect(screen.queryByLabelText(/^API key/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Continue with ChatGPT" }));
    expect(sent("POST", "/models/chatgpt/sign-in")).toEqual({});
  });

  it("says once that the plan is in use, and links to its usage", async () => {
    const plan = { ...models.chatgpt, state: "signed in", email: "ada@invented.invalid",
      welcome: "You're using your ChatGPT plan." };
    const conn = { api: "chatgpt", label: "ChatGPT plan", endpoint: null, key: null,
      models: [{ name: "gpt-invented-plan", model: "gpt-invented", default: false, used_by: [] }] };
    replies["/models"] = { ...models, connections: [...models.connections, conn], chatgpt: plan };
    replies["POST /models/chatgpt/welcomed"] = { ...models, connections: [...models.connections, conn],
      chatgpt: { ...plan, welcome: null } };
    render(<Settings />);
    expect(await screen.findByText("You're using your ChatGPT plan")).toBeInTheDocument();
    expect(screen.getByText("Signed in as ada@invented.invalid")).toBeInTheDocument();
    expect(screen.getByText("Using ChatGPT plan")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Got it" }));
    expect(calls.some(([m, p]) => m === "POST" && p === "/models/chatgpt/welcomed")).toBe(true);
    expect(screen.queryByText("You're using your ChatGPT plan")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Manage usage" })).toBeInTheDocument();
    expect(screen.getByText("ChatGPT plan")).toBeInTheDocument(); // in the providers list
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));
    expect(screen.queryByRole("option", { name: "ChatGPT plan (sign in, no key)" })).not.toBeInTheDocument(); // already there
  });
});

describe("Settings › General: known providers", () => {
  it("offers the providers not yet connected, and fills in the address and key name", async () => {
    replies["POST /models/providers"] = models;
    render(<Settings />);
    await userEvent.click(await screen.findByRole("button", { name: "Add provider" }));
    const picker = screen.getByLabelText("Provider");
    expect(within(picker).queryByText("Invented")).toBeNull(); // already connected
    expect((screen.getByLabelText(/^Address/) as HTMLInputElement).value).toBe("https://api.anthropic.com/v1");
    await userEvent.type(screen.getByLabelText(/^API key/), "sk-INVENTED");
    await userEvent.click(screen.getAllByRole("button", { name: "Add provider" })[0]);
    expect(sent("POST", "/models/providers")).toEqual({
      endpoint: "https://api.anthropic.com/v1", key: "sk-INVENTED", key_name: "ANTHROPIC_API_KEY", model: "",
    });
  });

  it("asks no key of a provider on this computer", async () => {
    render(<Settings />);
    await userEvent.click(await screen.findByRole("button", { name: "Add provider" }));
    await userEvent.selectOptions(screen.getByLabelText("Provider"), "http://localhost:11434/v1");
    expect(screen.queryByLabelText(/^API key/)).toBeNull();
    expect(screen.getByText(/needs no key/)).toBeTruthy();
  });

  it("says a local model runs on the server when Ombro does", async () => {
    useStore.setState({ remote: true });
    render(<Settings />);
    await userEvent.click(await screen.findByRole("button", { name: "Add provider" }));
    expect(screen.getByRole("option", { name: "Ollama, on the server" })).toBeTruthy();
    expect(screen.queryByText(/on this computer/)).toBeNull();
    await userEvent.selectOptions(screen.getByLabelText("Provider"), "http://localhost:11434/v1");
    expect(screen.getByText(/runs on the server/)).toBeTruthy();
  });
});

describe("Settings › Crews", () => {
  beforeEach(() => useStore.setState({ settingsPage: "crews" }));

  it("shows each member's kind and model, and renames a crew", async () => {
    replies["PUT /crews/legal"] = crews;
    render(<Settings />);
    const row = (await screen.findByText("Legal Assistant")).closest(".item") as HTMLElement;
    expect(within(row).getByText("Helper")).toBeInTheDocument();
    expect(within(row).getByText("Model: local-model")).toBeInTheDocument();
    expect(screen.queryByText(/Ombro Lead/)).not.toBeInTheDocument(); // the Lead isn't a crew member
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    const title = screen.getByLabelText("New title");
    await userEvent.clear(title);
    await userEvent.type(title, "Legal Team");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(sent("PUT", "/crews/legal")).toEqual({ title: "Legal Team" });
  });

  it("warns before deleting a member, and only then deletes", async () => {
    replies["/roles/legal/counsel/check"] = { blocked: null, warnings: ["An invented warning."] };
    replies["DELETE /roles/legal/counsel"] = crews;
    render(<Settings />);
    const row = (await screen.findByText("Legal Researcher")).closest(".item") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Delete" }));
    const ask = await screen.findByRole("group", { name: "Delete Legal Researcher?" });
    expect(ask).toHaveTextContent("An invented warning.");
    expect(calls.some(([m]) => m === "DELETE")).toBe(false);
    await userEvent.click(within(ask).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls.some(([m, p]) => m === "DELETE" && p === "/roles/legal/counsel")).toBe(true));
  });

  it("lists the same models as General, with providers, and any change wakes Save and Cancel", async () => {
    const counsel = { name: "counsel", crew: "legal", title: "Legal Researcher", kind: "specialist", model: "default",
      description: "Researches.", tools: [], skills: [], all_skills: false, soul: "You research.", path: "/x" };
    replies["GET /roles/legal/counsel"] = counsel;
    replies["PUT /roles/legal/counsel"] = { ...counsel, model: "local-model" };
    render(<Settings />);
    const row = (await screen.findByText("Legal Researcher")).closest(".item") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Edit" }));
    const model = await screen.findByLabelText("Model");
    const options = within(model).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["The default model", "invented-chat (Invented)", "local-model (Local)"]);
    const save = screen.getByRole("button", { name: "Save" });
    const cancel = screen.getByRole("button", { name: "Cancel" });
    expect(save).toBeDisabled();
    expect(cancel).toBeDisabled();
    await userEvent.selectOptions(model, "local-model");
    expect(save).toBeEnabled();
    expect(cancel).toBeEnabled();
    await userEvent.click(cancel);
    expect(model).toHaveValue("default");
    expect(save).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "helper");
    expect(save).toBeEnabled();
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "specialist");
    expect(save).toBeDisabled(); // back as it was: nothing to save
    await userEvent.selectOptions(model, "local-model");
    await userEvent.click(save);
    expect(sent("PUT", "/roles/legal/counsel")).toEqual({ model: "local-model" });
    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect(save).toBeDisabled();
  });

  it("renames a member and makes them a helper in one save, then follows the new name", async () => {
    const counsel = { name: "counsel", crew: "legal", title: "Legal Researcher", kind: "specialist", model: "default",
      description: "Researches.", tools: [], skills: [], all_skills: false, soul: "You research.", path: "/x" };
    const renamed = { ...counsel, name: "case-reader", title: "Case Reader", kind: "helper" };
    replies["GET /roles/legal/counsel"] = counsel;
    replies["PUT /roles/legal/counsel"] = renamed;
    replies["GET /roles/legal/case-reader"] = renamed;
    render(<Settings />);
    const row = (await screen.findByText("Legal Researcher")).closest(".item") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Edit" }));
    const name = await screen.findByLabelText("Name");
    await userEvent.clear(name);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled(); // a member needs a name
    await userEvent.type(name, "Case Reader");
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "helper");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(sent("PUT", "/roles/legal/counsel")).toEqual({ title: "Case Reader", kind: "helper" });
    expect(await screen.findByRole("heading", { name: "Legal Crew – Case Reader" })).toBeInTheDocument();
    await waitFor(() => expect(calls.some(([m, p]) => m === "GET" && p === "/roles/legal/case-reader")).toBe(true));
  });

  it("shows a refused rename", async () => {
    const counsel = { name: "counsel", crew: "legal", title: "Legal Researcher", kind: "specialist", model: "default",
      description: "Researches.", tools: [], skills: [], all_skills: false, soul: "You research.", path: "/x" };
    replies["GET /roles/legal/counsel"] = counsel;
    replies["PUT /roles/legal/counsel"] = new Error("The Legal Crew already has a member called Legal Assistant.");
    render(<Settings />);
    const row = (await screen.findByText("Legal Researcher")).closest(".item") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Edit" }));
    const name = await screen.findByLabelText("Name");
    await userEvent.clear(name);
    await userEvent.type(name, "Legal Assistant");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/already has a member called Legal Assistant/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled(); // the edit is still there
  });

  it("starts a new member as a chat", async () => {
    const newMember = vi.fn(async () => undefined);
    useStore.setState({ newMember });
    render(<Settings />);
    await userEvent.click(await screen.findByRole("button", { name: "New crew member" }));
    expect(newMember).toHaveBeenCalledWith("legal");
  });
});

describe("Settings › Folders, Tools and Skills", () => {
  it("lists folders by a short path and stops sharing one", async () => {
    useStore.setState({ settingsPage: "folders" });
    replies["DELETE /folders/big-inc"] = { ...folders, folders: [] };
    render(<Settings />);
    expect(await screen.findByText("~/Projects/big-inc")).toBeInTheDocument();
    expect(screen.getByText("Can read and write")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    await userEvent.click(screen.getByRole("button", { name: "Yes" }));
    await waitFor(() => expect(screen.getByText("No folders are shared yet.")).toBeInTheDocument());
  });

  it("adds an online tool with a secret header, then signs in", async () => {
    useStore.setState({ settingsPage: "tools" });
    const opened = vi.spyOn(window, "open").mockImplementation(() => null);
    replies["POST /tools/servers"] = { ...tools, added: "sheets" };
    replies["POST /tools/servers/sheets/sign-in"] = { url: "https://auth.invented.invalid/authorize", state: "waiting" };
    render(<Settings />);
    expect(await screen.findByText("Up to 60 calls a job")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add a tool" }));
    await userEvent.type(screen.getByLabelText(/^Name/), "sheets");
    await userEvent.type(screen.getByLabelText(/^Address/), "https://mcp.sheets.invalid");
    await userEvent.click(screen.getByRole("button", { name: "Add a header" }));
    await userEvent.type(screen.getByLabelText("Secret header name"), "Authorization");
    await userEvent.type(screen.getByLabelText("Secret header value"), "sk-INVENTED");
    await userEvent.click(screen.getByRole("button", { name: "Authenticate & Add" }));
    await waitFor(() => expect(opened).toHaveBeenCalledWith("https://auth.invented.invalid/authorize", "_blank", "noopener"));
    expect(sent("POST", "/tools/servers")).toEqual({ name: "sheets", url: "https://mcp.sheets.invalid", command: undefined,
      secrets: { Authorization: "sk-INVENTED" }, keep: [] });
    opened.mockRestore();
  });

  it("lists a web API and adds another, key and all", async () => {
    useStore.setState({ settingsPage: "tools" });
    replies["POST /tools/apis"] = tools;
    render(<Settings />);
    expect(await screen.findByText("Only looks things up")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add a tool" }));
    await userEvent.selectOptions(screen.getByLabelText("Type"), "api");
    await userEvent.type(screen.getByLabelText(/^Name/), "weather");
    await userEvent.type(screen.getByLabelText(/^Address/), "http://192.168.1.20/api");
    expect(screen.getByText(/the key travels unencrypted/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Key"), "wk-INVENTED");
    await userEvent.selectOptions(screen.getByLabelText("How the key is sent"), "header");
    await userEvent.type(screen.getByLabelText("Header name"), "X-API-Key");
    await userEvent.type(screen.getByLabelText(/^What it's for/), "Invented weather.");
    const gets = screen.getByLabelText("Do GET requests to this API only look things up?");
    expect(gets).toHaveValue("yes");
    await userEvent.selectOptions(gets, "no");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(sent("POST", "/tools/apis")).toEqual({ name: "weather", url: "http://192.168.1.20/api", key: "wk-INVENTED",
      send_key: "header", key_param: "X-API-Key", about: "Invented weather.", how_to: "", access: "external",
      get_looks_up: false, max_calls: 30 });
  });

  it("connects a Composio account by its key, and lists its services", async () => {
    useStore.setState({ settingsPage: "tools" });
    replies["PUT /tools/composio"] = { ...tools, composio: { key: "COMPOSIO_API_KEY", key_set: true, access: "external",
      problem: null, dashboard: "https://dashboard.composio.dev", toolkits: [
        { slug: "googledocs", title: "Google Docs", active: true, status: "active", tools: 31 },
        { slug: "gmail", title: "Gmail", active: false, status: "expired", tools: 60 }] } };
    render(<Settings />);
    await userEvent.click(await screen.findByRole("button", { name: "Connect my Composio account" }));
    await userEvent.type(screen.getByLabelText("Composio API key"), "ak_INVENTED");
    await userEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(sent("PUT", "/tools/composio")).toEqual({ key: "ak_INVENTED" });
    expect(await screen.findByText("Google Docs (via Composio)")).toBeInTheDocument();
    expect(screen.getByText("31 tools")).toBeInTheDocument();
    expect(screen.getByText(/Connection expired/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect my Composio account" })).not.toBeInTheDocument();
  });

  it("writes a new skill", async () => {
    useStore.setState({ settingsPage: "skills" });
    replies["POST /skills"] = library;
    render(<Settings />);
    expect(await screen.findByText("Legal Crew")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "New skill" }));
    await userEvent.type(screen.getByLabelText(/^Name/), "Tone check");
    await userEvent.type(screen.getByLabelText(/^What it does/), "Checks the tone.");
    await userEvent.type(screen.getByLabelText(/^Instructions/), "Read it aloud.");
    await userEvent.click(screen.getByRole("button", { name: "Add skill" }));
    expect(sent("POST", "/skills")).toEqual({ title: "Tone check", description: "Checks the tone.", body: "Read it aloud." });
  });

  it("takes a whole SKILL.md pasted into the instructions", async () => {
    useStore.setState({ settingsPage: "skills" });
    replies["POST /skills"] = library;
    render(<Settings />);
    await userEvent.click(await screen.findByRole("button", { name: "New skill" }));
    const add = screen.getByRole("button", { name: "Add skill" });
    expect(add).toBeDisabled();
    const pasted = "---\nname: tone-check\ndescription: Checks the tone.\n---\nRead it aloud.";
    await userEvent.click(screen.getByLabelText(/^Instructions/));
    await userEvent.paste(pasted);
    expect(add).toBeEnabled(); // the name and description come from its front matter
    await userEvent.click(add);
    expect(sent("POST", "/skills")).toEqual({ title: "", description: "", body: pasted });
  });

  it("adds a skill from a folder, then gives it to the members with a list", async () => {
    useStore.setState({ settingsPage: "skills" });
    const folder = { folder: "kettle-survey", path: "/Users/ada/Skills/kettle-survey",
      files: { "SKILL.md": "---\nname: kettle-survey\ndescription: Surveys kettles.\n---\nBoil one." }, skipped: ["scripts/boil.sh"] };
    readSkillFolder.mockResolvedValue(folder);
    const view = { name: "kettle-survey", replaces: false, flags: [], references: [], skipped: ["scripts/boil.sh"],
      text: folder.files["SKILL.md"], diff: null };
    replies["POST /skills/import"] = view;
    render(<Settings />);
    await userEvent.click(await screen.findByRole("button", { name: "Add from a folder" }));
    expect(await screen.findByText("Kettle survey: new to your library")).toBeInTheDocument();
    expect(screen.getByText(/Not copied .*scripts\/boil\.sh/)).toBeInTheDocument();
    expect(sent("POST", "/skills/import")).toEqual({ ...folder, add: false });
    replies["POST /skills/import"] = { ...view, ...library, added: "kettle-survey", kept: null, not_given: ["legal/counsel"] };
    await userEvent.click(screen.getByRole("button", { name: "Add skill" }));
    expect(await screen.findByText(/doesn't get it yet: Legal Researcher \(Legal Crew\)/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Give it to them" }));
    await waitFor(() => expect(sent("POST", "/skills/kettle-survey/give")).toEqual({ role: "legal/counsel" }));
    expect(await screen.findByText("Every crew member uses it now.")).toBeInTheDocument();
  });

  it("says when a folder can't be read", async () => {
    useStore.setState({ settingsPage: "skills" });
    readSkillFolder.mockRejectedValue("There's no SKILL.md in /Users/ada/Downloads. Choose the folder that holds it.");
    render(<Settings />);
    await userEvent.click(await screen.findByRole("button", { name: "Add from a folder" }));
    expect(await screen.findByText(/There's no SKILL.md/)).toBeInTheDocument();
  });
});

describe("Settings › Crews: the default", () => {
  it("makes another crew the default", async () => {
    useStore.setState({ settingsPage: "crews" });
    const two = { ...crews, crews: [...crews.crews, { name: "default", title: "Default Crew", description: "", default: true, roles: [] }] };
    replies["/crews"] = two;
    replies["PUT /crews/legal/default"] = { ...two, default_crew: "legal",
      crews: two.crews.map((c) => ({ ...c, default: c.name === "legal" })) };
    render(<Settings />);
    expect(await screen.findByText("Default crew")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Make default" }));
    expect(sent("PUT", "/crews/legal/default")).toEqual({});
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Make default" })).toHaveLength(1));
  });
});
