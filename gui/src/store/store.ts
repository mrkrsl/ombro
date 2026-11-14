// The one client-side store (plan 2): fed by serve's socket and HTTP replies. Screens read it
// and call its actions; nothing else talks to serve.

import { create } from "zustand";
import { client, events, handshake, type Client, type ServerMessage } from "../api/serve";
import type {
  ApprovalRow,
  ChatRow,
  CommandView,
  CountsMessage,
  InputReply,
  JobRow,
  Preferences,
  Report,
  ScheduleRow,
  SessionView,
  SpecRow,
  Summary,
} from "../api/types";
import { notify } from "../platform";
import { addLocal, addReport, apply, greeting, newConversation, type Conversation } from "./conversation";

export type FeedLine = { key: number; jobId: string; name: string; line: string; status: string };

export type Screen = "home" | "chat" | "jobs" | "approvals" | "scheduled" | "settings" | "report";
export type Theme = "light" | "dark";

type State = {
  status: "starting" | "connected" | "lost" | "failed";
  problem: string;
  api: Client | null;
  summary: Summary | null;
  chats: ChatRow[];
  commands: CommandView[];
  counts: CountsMessage | null;
  setupNeeded: string | null;
  screen: Screen;
  screenArgs: string;
  current: string | null; // the session on screen
  conversations: Record<string, Conversation>;
  titles: Record<string, string>; // session id: the chat's title
  sidebar: boolean;
  theme: Theme;
  notice: string; // a short line under the input, e.g. a failed send
  chatOn: string | null; // the saved chat on screen, by name
  inspector: boolean;
  prefs: Preferences | null;
  feed: FeedLine[]; // every job's progress since the window opened, newest last
  jobs: JobRow[];
  approvals: ApprovalRow[];
  schedules: ScheduleRow[];
  specs: SpecRow[];
  report: Report | null; // on the report page
};

type Actions = {
  start: () => Promise<void>;
  refresh: () => Promise<void>;
  receive: (m: ServerMessage) => void;
  goHome: () => void;
  show: (screen: Screen, args?: string) => void;
  send: (text: string) => Promise<void>;
  answer: (value: string | boolean, shown?: string) => Promise<void>;
  interrupt: () => Promise<void>;
  openChat: (name: string) => Promise<void>;
  setMode: (mode: "Chat" | "Brief") => Promise<void>;
  toggleSidebar: () => void;
  toggleInspector: () => void;
  setTheme: (theme: Theme, save?: boolean) => void;
  setPref: (key: "background_feed" | "notifications", value: boolean) => Promise<void>;
  load: (screen?: Screen) => Promise<void>;
  readReport: (jobId: string, inChat: boolean) => Promise<void>;
  decide: (approve: boolean, refs: string[]) => Promise<string[]>;
  stopJob: (jobId: string) => Promise<void>;
  call: <T>(fn: (api: Client) => Promise<T>) => Promise<T | undefined>;
};

let socket: WebSocket | null = null;
let started = false;
let feedKey = 0;
const FEED_KEEP = 300;

function systemTheme(): Theme {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export const useStore = create<State & Actions>((set, get) => {
  /** Open a new session on serve, follow it on the socket, and put it on screen. */
  async function openSession(body: Record<string, unknown>, lines: Conversation["lines"] = []): Promise<string> {
    const api = get().api!;
    const { session } = await api.post<{ session: SessionView }>("/sessions", body);
    const c = newConversation(session.id, session.kind, lines);
    set((s) => ({ conversations: { ...s.conversations, [session.id]: c }, current: session.id, screen: "chat" }));
    socket?.send(JSON.stringify({ type: "subscribe", sessions: { [session.id]: 0 } }));
    return session.id;
  }

  async function afterIdle(id: string) {
    const api = get().api;
    if (!api) return;
    const [view, chats] = await Promise.all([
      api.get<SessionView>(`/sessions/${id}`).catch(() => null),
      api.get<ChatRow[]>("/chats").catch(() => get().chats),
    ]);
    if (view?.chat) chatNames[id] = view.chat;
    set((s) => ({ chats, titles: view?.title ? { ...s.titles, [id]: view.title } : s.titles, chatOn: view?.chat ?? s.chatOn }));
  }

  function connect(hs: { port: number; token: string }) {
    socket = events(
      hs,
      (m) => get().receive(m),
      () => {
        set({ status: "lost" });
        setTimeout(() => connect(hs), 1000); // serve is still running: come back, and pick up what was missed
      },
    );
    socket.onopen = () => {
      set({ status: "connected" });
      const sessions = Object.fromEntries(Object.values(get().conversations).map((c) => [c.id, c.seq]));
      if (Object.keys(sessions).length) socket?.send(JSON.stringify({ type: "subscribe", sessions }));
    };
  }

  return {
    status: "starting",
    problem: "",
    api: null,
    summary: null,
    chats: [],
    commands: [],
    counts: null,
    setupNeeded: null,
    screen: "home",
    screenArgs: "",
    current: null,
    conversations: {},
    titles: {},
    sidebar: true,
    theme: systemTheme(),
    notice: "",
    chatOn: null,
    inspector: false,
    prefs: null,
    feed: [],
    jobs: [],
    approvals: [],
    schedules: [],
    specs: [],
    report: null,

    async start() {
      if (started) return; // React runs effects twice in development
      started = true;
      try {
        const hs = await handshake();
        const api = client(hs);
        set({ api });
        connect(hs);
        await get().refresh();
        const setup = await api.get<{ needed: string | null }>("/setup").catch(() => ({ needed: null }));
        set({ setupNeeded: setup.needed });
        if (setup.needed) {
          // First run: set-up is a conversation, as in the terminal.
          await openSession({ flow: "setup" });
        }
      } catch (e) {
        set({ status: "failed", problem: String(e instanceof Error ? e.message : e) });
      }
    },

    async refresh() {
      const api = get().api;
      if (!api) return;
      const [summary, chats, commands, prefs] = await Promise.all([
        api.get<Summary>("/summary").catch(() => null),
        api.get<ChatRow[]>("/chats").catch(() => []),
        api.get<CommandView[]>("/commands").catch(() => []),
        api.get<Preferences>("/preferences").catch(() => null),
      ]);
      set({ summary, chats, commands, prefs });
      if (prefs && (prefs.theme === "light" || prefs.theme === "dark")) get().setTheme(prefs.theme, false);
    },

    receive(m) {
      if (m.type === "counts") {
        const before = get().counts;
        set({ counts: m });
        if (before && (before.waiting !== m.waiting || before.active !== m.active || before.scheduled !== m.scheduled)) {
          void get().load();
        }
        return;
      }
      if (m.type === "job") {
        const line: FeedLine = { key: ++feedKey, jobId: m.job_id, name: m.name, line: m.line, status: m.status };
        set((s) => ({ feed: [...s.feed, line].slice(-FEED_KEEP) }));
        if (m.event.type === "job_ended") {
          const e = m.event;
          if (get().prefs?.notifications !== false) {
            const waiting = e.waiting ? ` ${e.waiting} waiting for your approval.` : "";
            void notify(e.name, `${e.text}${waiting}`);
          }
          void get().load();
          void get().refresh();
        }
        return;
      }
      if (!("session" in m) || !m.session) return;
      const id = m.session;
      const c = get().conversations[id];
      if (!c) return;
      const next = apply(c, m);
      if (next === c) return;
      set((s) => ({ conversations: { ...s.conversations, [id]: next } }));
      if (m.type === "session" && (m.state === "idle" || m.state === "ended")) {
        void afterIdle(id);
        if (c.kind === "setup") {
          // Set-up has finished (or stopped): see whether Ombro is ready now.
          void get().api?.get<{ needed: string | null }>("/setup").then((r) => set({ setupNeeded: r.needed }));
          void get().refresh();
        }
      }
    },

    goHome() {
      set({ screen: "home", current: null, notice: "", chatOn: null, report: null });
      void get().refresh();
    },

    show(screen, args = "") {
      set({ screen, screenArgs: args });
      if (screen === "report" && args) void get().readReport(args, false);
      else void get().load(screen);
    },

    async load(screen = get().screen) {
      const api = get().api;
      if (!api) return;
      try {
        if (screen === "jobs") set({ jobs: (await api.get<JobRow[]>("/jobs")).slice().reverse() });
        if (screen === "approvals") set({ approvals: await api.get<ApprovalRow[]>("/approvals") });
        if (screen === "scheduled") {
          const [schedules, specs] = await Promise.all([api.get<ScheduleRow[]>("/schedules"), api.get<SpecRow[]>("/specs")]);
          set({ schedules, specs });
        }
        if (screen === "chat" || get().inspector) {
          // The inspector shows the chat's job and the approvals waiting for it.
          const [jobs, approvals, schedules] = await Promise.all([
            api.get<JobRow[]>("/jobs"),
            api.get<ApprovalRow[]>("/approvals"),
            api.get<ScheduleRow[]>("/schedules"),
          ]);
          set({ jobs: jobs.slice().reverse(), approvals, schedules });
        }
      } catch (e) {
        set({ notice: e instanceof Error ? e.message : String(e) });
      }
    },

    async readReport(jobId, inChat) {
      const api = get().api;
      if (!api) return;
      try {
        const report = await api.get<Report>(`/jobs/${encodeURIComponent(jobId)}/report`);
        void api.post(`/jobs/${encodeURIComponent(jobId)}/seen`).catch(() => undefined);
        const id = get().current;
        if (inChat && id && get().conversations[id]) {
          set((s) => ({ conversations: { ...s.conversations, [id]: addReport(s.conversations[id], report) } }));
        } else {
          set({ report, screen: "report", screenArgs: jobId });
        }
      } catch (e) {
        set({ notice: e instanceof Error ? e.message : String(e) });
      }
    },

    async decide(approve, refs) {
      const api = get().api;
      if (!api || !refs.length) return [];
      try {
        const r = await api.post<{ results: { ref: string; status: string; error?: string }[] }>(
          approve ? "/approvals/approve" : "/approvals/decline",
          { refs },
        );
        await get().load("approvals");
        return r.results.filter((x) => x.error).map((x) => `${x.ref}: ${x.error}`);
      } catch (e) {
        return [e instanceof Error ? e.message : String(e)];
      }
    },

    async stopJob(jobId) {
      await get().call((api) => api.post(`/jobs/${encodeURIComponent(jobId)}/stop`));
      await get().load("jobs");
    },

    async call(fn) {
      const api = get().api;
      if (!api) return undefined;
      try {
        set({ notice: "" });
        return await fn(api);
      } catch (e) {
        set({ notice: e instanceof Error ? e.message : String(e) });
        return undefined;
      }
    },

    async send(text) {
      const trimmed = text.trim();
      if (!trimmed) return;
      const { api, current, conversations, summary } = get();
      if (!api) return;
      set({ notice: "" });
      // Commands for the window or one of its pages need no conversation.
      const word = trimmed.split(/\s+/)[0].toLowerCase();
      const cmd = trimmed.startsWith("/") ? get().commands.find((c) => c.name === word) : undefined;
      const args = trimmed.slice(word.length).trim();
      if (cmd && (cmd.where === "app" || (cmd.where === "screen" && (!args || ["report", "open"].includes(cmd.screen))))) {
        if (cmd.where === "app") appCommand(cmd.name);
        else get().show(cmd.screen.startsWith("settings") ? "settings" : (cmd.screen as Screen), args);
        return;
      }
      try {
        let id = current;
        if (!id || !conversations[id] || conversations[id].state === "ended") {
          // From the home screen: the greeting carries into the new chat.
          id = await openSession({}, [{ key: "local-0", kind: "lead", text: greeting(summary?.name ?? "") }]);
        }
        set({ screen: "chat" }); // typed on a page: the conversation comes back
        const c = get().conversations[id];
        if (c.question) {
          await get().answer(trimmed);
          return;
        }
        set((s) => ({ conversations: { ...s.conversations, [id!]: addLocal(s.conversations[id!], "you", trimmed) } }));
        const reply = await api.post<InputReply>(`/sessions/${id}/input`, { text: trimmed });
        if (reply.route) {
          const r = reply.route;
          if (r.where === "app") return appCommand(r.command);
          const screen = r.screen.startsWith("settings") ? "settings" : (r.screen as Screen);
          get().show(screen, r.args);
        }
      } catch (e) {
        set({ notice: e instanceof Error ? e.message : String(e) });
      }

      function appCommand(command: string) {
        if (command === "/theme") get().setTheme(get().theme === "dark" ? "light" : "dark");
        else if (command === "/help") {
          const id = get().current;
          if (!id) return;
          const lines = get()
            .commands.map((c) => `${c.name}  ${c.help}`)
            .join("\n");
          set((s) => ({ conversations: { ...s.conversations, [id]: addLocal(s.conversations[id], "lead", "Commands:\n\n```\n" + lines + "\n```") } }));
        }
      }
    },

    async answer(value, shown) {
      const { api, current, conversations } = get();
      if (!api || !current) return;
      const q = conversations[current]?.question;
      if (!q) return;
      const label = shown ?? (q.field.kind === "secret" ? "•".repeat(String(value).length) : String(value || "(Enter)"));
      if (!(q.field.kind === "secret" && !value)) {
        set((s) => ({ conversations: { ...s.conversations, [current]: addLocal(s.conversations[current], "you", label) } }));
      }
      try {
        await api.post(`/sessions/${current}/answer`, { id: q.id, value });
      } catch (e) {
        set({ notice: e instanceof Error ? e.message : String(e) });
      }
    },

    async interrupt() {
      const { api, current } = get();
      if (api && current) await api.post(`/sessions/${current}/interrupt`).catch(() => undefined);
    },

    async openChat(name) {
      const existing = Object.values(get().conversations).find((c) => c.state !== "ended" && chatOf(c.id) === name);
      if (existing) {
        set({ current: existing.id, screen: "chat", chatOn: name });
        return;
      }
      try {
        const id = await openSession({ chat: name });
        chatNames[id] = name;
        set({ chatOn: name });
        const row = get().chats.find((c) => c.name === name);
        if (row) set((s) => ({ titles: { ...s.titles, [id]: row.title } }));
      } catch (e) {
        set({ notice: e instanceof Error ? e.message : String(e) });
      }
    },

    async setMode(mode) {
      const { current, conversations } = get();
      const c = current ? conversations[current] : null;
      if (c && c.mode === mode) return;
      if (mode === "Brief") {
        if (c?.question || c?.state === "working") return;
        await get().send("/brief");
      } else if (c?.question) {
        await get().answer("/chat"); // leaves the brief, with nothing written
      }
    },

    toggleSidebar() {
      set((s) => ({ sidebar: !s.sidebar }));
    },

    toggleInspector() {
      set((s) => ({ inspector: !s.inspector }));
      if (get().inspector) void get().load();
    },

    async setPref(key, value) {
      const prefs = await get().call((api) => api.put<Preferences>("/preferences", { [key]: value }));
      if (prefs) set({ prefs });
    },

    setTheme(theme, save = true) {
      set({ theme });
      if (save) void get().api?.put("/preferences", { theme });
    },
  };
});

// Which saved chat each session shows, so picking it again in the sidebar returns to it.
const chatNames: Record<string, string> = {};
function chatOf(id: string): string | undefined {
  return chatNames[id];
}
