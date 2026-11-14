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
import { signInText } from "./Tools";
import { shortPath } from "./common";

type Call = [string, string, unknown?];

const prefs: Preferences = {
  name: "Ada", theme: "auto", default_crew: "default", default_crew_title: "Default Crew", editor: "",
  outward: "balanced", instructions_set: true, instructions: "I run an invented bakery.", background_feed: true,
  notifications: true, files: {},
};
const models: ModelList = {
  default: "invented-chat",
  connections: [
    { api: "openai", label: "Invented", endpoint: "https://api.invented.invalid", key: "INVENTED_API_KEY",
      models: [{ name: "invented-chat", model: "invented-chat", default: true, used_by: [] }] },
    { api: "openai", label: "Local", endpoint: "http://192.168.1.100:1234/v1", key: null,
      models: [{ name: "local-model", model: "local-model", default: false, used_by: [] }] },
  ],
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
    { tool: "web.fetch", provider: "direct", max_calls: null, calls: 100 }],
  servers: [remote], sandbox: null, folders: [],
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

beforeEach(() => {
  calls = [];
  replies = { "/preferences": prefs, "/models": models, "/limits": { allowance: "moderate" }, "/crews": crews,
    "/folders": folders, "/tools": tools, "/skills": library, "/keys": { keys: [], sign_ins: [], file: "/x" } };
  useStore.setState({ api: fakeApi(), settingsPage: "general", screen: "settings" });
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
    await userEvent.type(screen.getByLabelText(/^Address/), "https://api.other.invalid");
    await userEvent.type(screen.getByLabelText(/^API key/), "sk-INVENTED");
    await userEvent.click(screen.getAllByRole("button", { name: "Add provider" })[0]);
    expect(sent("POST", "/models/providers")).toEqual({ endpoint: "https://api.other.invalid", key: "sk-INVENTED", model: "" });
    replies["PUT /preferences"] = { ...prefs, outward: "manual" };
    await userEvent.selectOptions(screen.getByLabelText("Approval mode"), "manual");
    expect(sent("PUT", "/preferences")).toEqual({ outward: "manual" });
  });
});

describe("Settings › Crews", () => {
  beforeEach(() => useStore.setState({ settingsPage: "crews" }));

  it("shows each member's kind and model, and renames a crew", async () => {
    replies["PUT /crews/legal"] = crews;
    render(<Settings />);
    const row = (await screen.findByText("Legal Assistant")).closest(".row") as HTMLElement;
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
    const row = (await screen.findByText("Legal Researcher")).closest(".row") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Delete" }));
    const ask = await screen.findByRole("group", { name: "Delete Legal Researcher?" });
    expect(ask).toHaveTextContent("An invented warning.");
    expect(calls.some(([m]) => m === "DELETE")).toBe(false);
    await userEvent.click(within(ask).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls.some(([m, p]) => m === "DELETE" && p === "/roles/legal/counsel")).toBe(true));
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
    expect(screen.getByText("Can read and change")).toBeInTheDocument();
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
});
