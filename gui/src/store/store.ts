// The one client-side store (plan 2): fed by serve's socket and HTTP replies. Screens read it
// and call its actions; nothing else talks to serve.

import { create } from "zustand";
import { client, events, handshake, inTauri, type Attachment, type Client, type ServerMessage } from "../api/serve";
import type {
  ApprovalRow,
  Brief,
  ChatRow,
  CommandView,
  CountsMessage,
  InputReply,
  JobRow,
  ModelList,
  Preferences,
  Report,
  ScheduleRow,
  SchedulerState,
  SessionView,
  SpecRow,
  Summary,
} from "../api/types";
import { isMac, notify, openFile, openLink, revealFile, whenVisible } from "../platform";
import { readCopy, saveCopy, saveFolder } from "../copies";
import { PROTOCOL, startConnection, tooOld, whereNow, type Where } from "../where";
import { SHORTCUTS, shortcutText } from "../shortcuts";
import { addLocal, addReport, apply, newConversation, opening, type Conversation } from "./conversation";

const MAX_ATTACH_BYTES = 25 * 1024 * 1024; // the most serve takes for one file

/** A toast, in the window's corner. Timed (`ms`), dismissable, both, or neither: a persistent one
 *  (such as "Reconnecting…") stays until whatever showed it takes it away. One per id: showing an
 *  id again replaces it. */
export type Toast = { id: string; text: string; tone: "info" | "error"; dismissable: boolean; ms?: number };
/** How long a notice stays. A problem stays until it's dismissed, or the next thing you do. */
export const NOTICE_MS = 6000;
/** How long "Restarting…" shows before the app restarts, so the switch doesn't look like a crash. */
export const RESTART_PAUSE_MS = 900;

/** How long a finished job's lines stay in the side panel's background work (they stay in its record). */
export const FEED_CLEAR_MS = 60_000;

// at: when the line came in (ms), shown in front of it in the side panel.
export type FeedLine = { key: number; jobId: string; name: string; line: string; status: string; at: number };

export type Screen = "home" | "chat" | "jobs" | "approvals" | "scheduled" | "settings" | "report" | "brief";
export type Theme = "light" | "dark";
export type ThemeChoice = "auto" | "light" | "dark"; // auto: as the system
export type SettingsPage = "general" | "crews" | "folders" | "tools" | "skills";
const SETTINGS_PAGES: SettingsPage[] = ["general", "crews", "folders", "tools", "skills"];

/** The screen a command names ("settings/crews"), with the Settings page it opens at, if any. */
export function screenFor(name: string): { screen: Screen; page?: SettingsPage } {
  if (name.startsWith("settings")) {
    const page = name.split("/")[1] as SettingsPage;
    return { screen: "settings", page: SETTINGS_PAGES.includes(page) ? page : undefined };
  }
  return { screen: name as Screen };
}

type State = {
  status: "starting" | "choose" | "connected" | "lost" | "failed";
  where: Where | null; // where Ombro runs (app only)
  problem: string;
  api: Client | null;
  summary: Summary | null;
  models: ModelList | null; // the model connections, to name models as the Crews page does
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
  themeChoice: ThemeChoice;
  settingsPage: SettingsPage;
  toasts: Toast[]; // in the corner, newest last: notices, problems, "Reconnecting…"
  restarting: boolean; // switching where Ombro runs: the app is about to restart
  chatOn: string | null; // the saved chat on screen, by name
  inspector: boolean;
  prefs: Preferences | null;
  feed: FeedLine[]; // every job's progress since the window opened, newest last
  jobs: JobRow[];
  approvals: ApprovalRow[];
  schedules: ScheduleRow[];
  scheduler: SchedulerState | null; // whether the scheduler service is there to run them
  specs: SpecRow[];
  report: Report | null; // on the report page
  brief: Brief | null; // on the brief page: the brief a job ran from
  attached: Attachment[]; // files waiting to go with the next message or answer
  attaching: number; // uploads under way
  opener: string | null; // after New from a chat you'd used: the Lead's line that it was saved, in place of the home screen
  welcome: boolean; // a fresh install: set-up's first question waits under the home screen
  running: string[]; // the jobs running now, by id: a chat that started one shows the ring in the sidebar
  remote: boolean; // serve is on another machine: its files come here as copies ("Save to this computer")
  copies: Record<string, string>; // a server file: where its copy is kept, under ~/Downloads/Ombro
};

type Actions = {
  start: () => Promise<void>;
  attach: () => Promise<void>;
  connectNow: () => Promise<void>;
  refresh: () => Promise<void>;
  receive: (m: ServerMessage) => void;
  goHome: () => void;
  resumeSetup: () => void;
  show: (screen: Screen, args?: string) => void;
  send: (text: string) => Promise<void>;
  answer: (value: string | boolean, shown?: string) => Promise<void>;
  interrupt: () => Promise<void>;
  doneEditing: () => Promise<void>;
  openChat: (name: string) => Promise<void>;
  /** Delete a chat from the list: the server moves it to the chats folder's Archive/, unchanged. */
  deleteChat: (name: string) => Promise<void>;
  setMode: (mode: "Chat" | "Brief") => Promise<void>;
  toggleSidebar: () => void;
  toggleInspector: () => void;
  setTheme: (theme: Theme, save?: boolean) => void;
  setThemeChoice: (choice: ThemeChoice) => void;
  showSettings: (page: SettingsPage) => void;
  newMember: (crew: string) => Promise<void>;
  setPref: (key: "background_feed" | "notifications", value: boolean) => Promise<void>;
  load: (screen?: Screen) => Promise<void>;
  readReport: (jobId: string, inChat: boolean) => Promise<void>;
  readBrief: (jobId: string) => Promise<void>;
  /** Files dropped on the window or chosen with the paperclip: kept by Ombro, shown as chips, sent with the next message. */
  attachFiles: (files: File[]) => Promise<void>;
  unattach: (path: string) => void;
  /** Discuss this brief: a new chat, where the Lead has the brief a job ran from. */
  discussBrief: (jobId: string) => Promise<void>;
  clearJobs: (jobId?: string) => Promise<void>;
  restoreJob: (jobId: string) => Promise<void>;
  decide: (approve: boolean, refs: string[]) => Promise<string[]>;
  stopJob: (jobId: string) => Promise<void>;
  call: <T>(fn: (api: Client) => Promise<T>) => Promise<T | undefined>;
  openPath: (path: string, how: "open" | "reveal") => Promise<void>;
  openFolder: (path: string) => Promise<void>;
  openHref: (href: string) => Promise<void>;
  toast: (t: Omit<Toast, "tone" | "dismissable"> & Partial<Toast>) => void;
  dismissToast: (id: string) => void;
  /** The one notice (a failed send, "That job made no files."): replaces the last; "" clears it. */
  say: (text: string, tone?: Toast["tone"]) => void;
};

let socket: WebSocket | null = null;
let started = false;
let feedKey = 0;
let jobsFetched = 0;
const FEED_KEEP = 300;

function systemTheme(): Theme {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export const useStore = create<State & Actions>((set, get) => {
  /** Open a new session on serve, follow it on the socket, and put it on screen. */
  async function openSession(body: Record<string, unknown>, lines: Conversation["lines"] = [], stay = false): Promise<string> {
    const api = get().api!;
    const { session } = await api.post<{ session: SessionView }>("/sessions", body);
    const c = newConversation(session.id, session.kind, lines);
    set((s) => ({
      conversations: { ...s.conversations, [session.id]: c },
      current: session.id,
      // A new session shows no saved chat until it has one (openChat names it): the side panel
      // must not go on showing the chat, or the job, that was on screen before.
      chatOn: null,
      screen: stay ? s.screen : "chat",
    }));
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
    set((s) => ({
      chats,
      titles: view?.title ? { ...s.titles, [id]: view.title } : s.titles,
      // Only the session on screen says which chat is on screen; one working in the background doesn't.
      chatOn: s.current === id && view?.chat ? view.chat : s.chatOn,
    }));
  }

  function connect(hs: { port: number; token: string }) {
    socket = events(
      hs,
      (m) => get().receive(m),
      () => {
        set({ status: "lost" });
        // Until the socket is back: not timed, and not dismissable, since it clears itself.
        get().toast({ id: "lost", text: get().remote ? "Reconnecting to the server…" : "Reconnecting to Ombro…" });
        setTimeout(() => connect(hs), 1000); // serve is still running: come back, and pick up what was missed
      },
    );
    socket.onopen = () => {
      set({ status: "connected" });
      get().dismissToast("lost");
      const sessions = Object.fromEntries(Object.values(get().conversations).map((c) => [c.id, c.seq]));
      if (Object.keys(sessions).length) socket?.send(JSON.stringify({ type: "subscribe", sessions }));
    };
  }

  return {
    status: "starting",
    where: null,
    problem: "",
    api: null,
    summary: null,
    models: null,
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
    themeChoice: "auto",
    settingsPage: "general",
    toasts: [],
    restarting: false,
    chatOn: null,
    inspector: false,
    prefs: null,
    feed: [],
    jobs: [],
    approvals: [],
    schedules: [],
    scheduler: null,
    specs: [],
    report: null,
    brief: null,
    attached: [],
    attaching: 0,
    opener: null,
    welcome: false,
    running: [],
    remote: false,
    copies: {},

    async start() {
      if (started) return; // React runs effects twice in development
      started = true;
      try {
        const where = await whereNow();
        set({ where });
        if (where && !where.started) {
          set({ status: "choose" }); // a fresh install: where should Ombro run?
          return;
        }
      } catch (e) {
        set({ status: "failed", problem: String(e instanceof Error ? e.message : e) });
        return;
      }
      await get().attach();
    },

    async connectNow() {
      // After choosing (or pairing): start it now, or restart the app to switch to it. A restart
      // says so first, or the window would vanish as if it had crashed.
      if (get().where?.started) {
        set({ restarting: true });
        await new Promise((r) => setTimeout(r, RESTART_PAUSE_MS));
      }
      set({ status: "starting", problem: "" });
      try {
        await startConnection();
        set({ where: await whereNow() });
        await get().attach();
      } catch (e) {
        set({ status: "failed", problem: String(e instanceof Error ? e.message : e), restarting: false });
      }
    },

    async attach() {
      try {
        const hs = await handshake();
        const api = client(hs);
        let hello: { version?: string; protocol?: number; notes?: string[] } = {};
        if (hs.remote) {
          // A server: check it's one this app can work with, and that it still knows this computer.
          try {
            hello = await api.get<{ version?: string; protocol?: number; notes?: string[] }>("/hello");
          } catch (e) {
            const status = (e as { status?: number }).status;
            throw new Error(
              status === 403
                ? "The server has forgotten its connection with this computer. Connect again to pair it."
                : "The server didn't answer. Check that Ombro is running there.",
            );
          }
          if (hello.protocol !== PROTOCOL) throw new Error(tooOld(hello.version));
        } else {
          hello = await api.get<{ notes?: string[] }>("/hello").catch(() => ({}));
        }
        set({ api, remote: Boolean(hs.remote) });
        // What Ombro did to bring this home up to date as it started (models moved to a provider's own API).
        for (const [i, text] of (hello.notes ?? []).entries()) {
          if (shownNotes.has(text)) continue; // once, not again on each reconnect
          shownNotes.add(text);
          get().toast({ id: `start-note-${i}`, text, dismissable: true });
        }
        connect(hs);
        await get().refresh();
        const setup = await api.get<{ needed: string | null }>("/setup").catch(() => ({ needed: null }));
        set({ setupNeeded: setup.needed });
        if (setup.needed) {
          // First run: set-up is a conversation, as in the terminal. Its first question (what to
          // call you) is asked under the home screen, which shows the team set-up has made.
          set({ welcome: true });
          await openSession({ flow: "setup" }, [], true);
        }
      } catch (e) {
        set({ status: "failed", problem: String(e instanceof Error ? e.message : e) });
      }
    },

    async refresh() {
      const api = get().api;
      if (!api) return;
      const [summary, chats, commands, prefs, jobs, models] = await Promise.all([
        api.get<Summary>("/summary").catch(() => null),
        api.get<ChatRow[]>("/chats").catch(() => []),
        api.get<CommandView[]>("/commands").catch(() => []),
        api.get<Preferences>("/preferences").catch(() => null),
        api.get<JobRow[]>("/jobs").catch(() => null),
        api.get<ModelList>("/models").catch(() => null),
      ]);
      set({ summary, chats, commands, prefs, models });
      if (Array.isArray(jobs)) set({ running: jobs.filter((j) => j.active).map((j) => j.job_id) });
      if (prefs) {
        const choice: ThemeChoice = prefs.theme === "light" || prefs.theme === "dark" ? prefs.theme : "auto";
        set({ themeChoice: choice, theme: choice === "auto" ? systemTheme() : choice });
      }
    },

    receive(m) {
      if (m.type === "counts") {
        const before = get().counts;
        // The jobs running now come with the counts, so a chat's ring shows from the moment its job
        // starts (not at its first step), and for a live job too, whose chat stays busy until it ends.
        const running = Array.isArray(m.running) ? m.running : get().running;
        set({ counts: m, running });
        const known = new Set(get().chats.flatMap((c) => c.jobs));
        const api = get().api;
        if (api && running.some((id) => !known.has(id))) {
          // A job the sidebar doesn't know the chat of: the chat's record names it by now.
          void api.get<ChatRow[]>("/chats").then((chats) => set({ chats }), () => undefined);
        }
        if (before && (before.waiting !== m.waiting || before.active !== m.active || before.scheduled !== m.scheduled)) {
          void get().load();
        }
        return;
      }
      if (m.type === "job") {
        const line: FeedLine = {
          key: ++feedKey, jobId: m.job_id, name: m.name, line: m.line, status: m.status, at: Date.now(),
        };
        set((s) => ({
          feed: [...s.feed, line].slice(-FEED_KEEP),
          running:
            m.status === "running"
              ? s.running.includes(m.job_id) ? s.running : [...s.running, m.job_id]
              : s.running.filter((id) => id !== m.job_id),
        }));
        // The inspector's job usage keeps up with the job's steps, a few seconds behind at most.
        const api = get().api;
        if (api && (get().screen === "chat" || get().inspector) && Date.now() - jobsFetched > 4000) {
          jobsFetched = Date.now();
          void api.get<JobRow[]>("/jobs").then((jobs) => set({ jobs: jobs.slice().reverse() }), () => undefined);
        }
        if (m.event.type === "job_ended") {
          const e = m.event;
          // A minute on, the panel lets go of the finished job: its lines up to the ending, unless
          // it is going again by then (carried on under the same id).
          setTimeout(() => {
            if (get().running.includes(m.job_id)) return;
            set((s) => ({ feed: s.feed.filter((f) => f.jobId !== m.job_id || f.key > line.key) }));
          }, FEED_CLEAR_MS);
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
      if (m.type === "chat_saved") {
        // The chat is on disk from its first message (T21): it joins the list of chats at once, so
        // clicking away while the Lead answers, or asks a question, never loses it.
        chatNames[id] = m.chat;
        if (get().current === id) set({ chatOn: m.chat });
        void afterIdle(id);
        return;
      }
      const c = get().conversations[id];
      if (!c) return;
      const next = apply(c, m);
      if (next === c) return;
      set((s) => ({ conversations: { ...s.conversations, [id]: next } }));
      // Set-up changes what the home screen and footer show (the home, then the model): keep up.
      if (m.type === "question" && (get().welcome || c.kind === "setup")) void get().refresh();
      if (m.type === "event" && m.event.type === "open_page") {
        // Ombro suggests a page (Schedule it, after a live job): go there, with the brief picked.
        get().show(m.event.screen as Screen, m.event.args);
      }
      // A session waiting on you (an interview's question) has been saved by then: the chat list
      // and the side panel learn of it, so you can click away and come back to it.
      if (m.type === "session" && m.state === "waiting" && !chatOf(id)) void afterIdle(id);
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
      // New, from a chat you'd typed in: as in the terminal, the Lead says it's saved and asks
      // what's next, on an empty page. From anywhere else: the home screen.
      const { current, conversations, titles } = get();
      if (get().welcome) {
        get().say("");
        set({ screen: "home", report: null }); // the welcome still waits for its answer
        return;
      }
      const used = !!current && !!conversations[current]?.lines.some((l) => l.kind === "you");
      const title = current ? titles[current] : "";
      const opener = used
        ? `${title ? `I've saved the last chat, '${title}'.` : "I've saved the last chat."} This is a new chat. How would you like me to help?`
        : null;
      get().say("");
      set({ screen: "home", current: null, chatOn: null, report: null, opener });
      void get().refresh();
    },

    resumeSetup() {
      // Set-up runs on while you look at a page: the sidebar brings you back to it.
      const c = Object.values(get().conversations).find((x) => x.kind === "setup" && x.state !== "ended");
      if (c) set({ current: c.id, screen: get().welcome ? "home" : "chat", chatOn: null });
    },

    show(screen, args = "") {
      // A notice belongs to the page it came from (such as "That job made no files." on Jobs).
      if (screen !== get().screen) get().say("");
      set(screen === get().screen ? { screenArgs: args } : { screen, screenArgs: args });
      if (screen === "report" && args) void get().readReport(args, false);
      else if (screen === "brief" && args) void get().readBrief(args);
      else void get().load(screen);
    },

    async load(screen = get().screen) {
      const api = get().api;
      if (!api) return;
      try {
        if (screen === "jobs") {
          const rows = await api.get<JobRow[]>("/jobs");
          set({ jobs: rows.slice().reverse() });
          // Looking at the Jobs page sees its finished jobs: their "new" stays for this visit, and
          // the menu bar's "job completed" and alert clear.
          if (rows.some((j) => !j.seen_at && !j.active && j.status !== "running"))
            whenVisible(() => {
              if (get().screen === "jobs") void api.post("/jobs/seen").catch(() => undefined);
            });
        }
        if (screen === "approvals") set({ approvals: await api.get<ApprovalRow[]>("/approvals") });
        if (screen === "scheduled") {
          const [schedules, specs, scheduler] = await Promise.all([
            api.get<ScheduleRow[]>("/schedules"),
            api.get<SpecRow[]>("/specs"),
            api.get<SchedulerState>("/scheduler"),
          ]);
          set({ schedules, specs, scheduler });
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
        get().say(e instanceof Error ? e.message : String(e), "error");
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
        get().say(e instanceof Error ? e.message : String(e), "error");
      }
    },

    async attachFiles(files) {
      const api = get().api;
      if (!api) return;
      for (const f of files) {
        if (f.size > MAX_ATTACH_BYTES) {
          get().say(`${f.name} is over 25 MB, too large to attach.`, "error");
          continue;
        }
        set((s) => ({ attaching: s.attaching + 1 }));
        try {
          const a = await api.attach(f.name, f);
          set((s) => ({ attached: [...s.attached, a] }));
        } catch (e) {
          get().say(`${f.name} wasn't attached: ${e instanceof Error ? e.message : String(e)}`, "error");
        } finally {
          set((s) => ({ attaching: s.attaching - 1 }));
        }
      }
    },

    unattach(path) {
      set((s) => ({ attached: s.attached.filter((a) => a.path !== path) }));
    },

    async discussBrief(jobId) {
      try {
        await openSession({ discuss: jobId });
      } catch (e) {
        get().say(e instanceof Error ? e.message : String(e), "error");
      }
    },

    async readBrief(jobId) {
      const api = get().api;
      if (!api) return;
      if (get().brief?.job_id !== jobId) set({ brief: null });
      try {
        const brief = await api.get<Brief>(`/jobs/${encodeURIComponent(jobId)}/brief`);
        set({ brief, screen: "brief", screenArgs: jobId });
      } catch (e) {
        get().say(e instanceof Error ? e.message : String(e), "error");
      }
    },

    async clearJobs(jobId) {
      // Cleared jobs are only hidden from the Jobs page; their folders stay.
      const r = await get().call((api) =>
        api.post<{ cleared: number | boolean }>(jobId ? `/jobs/${encodeURIComponent(jobId)}/clear` : "/jobs/clear"),
      );
      if (r && !jobId) {
        const n = Number(r.cleared);
        get().say(n ? `Cleared ${n} finished job${n === 1 ? "" : "s"}.` : "No finished jobs to clear.");
      }
      await get().load("jobs");
    },

    async restoreJob(jobId) {
      await get().call((api) => api.post(`/jobs/${encodeURIComponent(jobId)}/restore`));
      await get().load("jobs");
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
        get().say("");
        return await fn(api);
      } catch (e) {
        get().say(e instanceof Error ? e.message : String(e), "error");
        return undefined;
      }
    },

    async send(text) {
      const files = get().attached;
      // Files alone are a message too: it says what was attached.
      const trimmed = text.trim() || (files.length ? `I've attached ${files.map((a) => a.name).join(", ")}.` : "");
      if (!trimmed) return;
      const { api, current, conversations, summary } = get();
      if (!api) return;
      get().say("");
      // Commands for the window or one of its pages need no conversation.
      const word = trimmed.split(/\s+/)[0].toLowerCase();
      const cmd = trimmed.startsWith("/") ? get().commands.find((c) => c.name === word) : undefined;
      const args = trimmed.slice(word.length).trim();
      if (cmd && (cmd.where === "app" || (cmd.where === "screen" && (!args || ["report", "open"].includes(cmd.screen))))) {
        if (cmd.where === "app") appCommand(cmd.name);
        else openScreen(cmd.screen, args);
        return;
      }
      try {
        let id = current;
        if (!id || !conversations[id] || conversations[id].state === "ended") {
          // From the home screen: the greeting carries into the new chat.
          const first = get().opener ?? opening(summary?.name ?? "", get().chats.length === 0);
          id = await openSession({}, [{ key: "local-0", kind: "lead", text: first }]);
        }
        set({ screen: "chat" }); // typed on a page: the conversation comes back
        const c = get().conversations[id];
        if (c.question) {
          // A command mid-question: /chat leaves a brief; the others wait until it's answered.
          if (cmd && cmd.name !== "/chat") {
            get().say(`Answer the question first, or press Esc to stop, then ${cmd.name}.`);
            return;
          }
          await get().answer(trimmed);
          return;
        }
        const shown = files.length ? `${trimmed}\n\n${files.map((a) => `📎 ${a.name}`).join("  ")}` : trimmed;
        set((s) => ({ conversations: { ...s.conversations, [id!]: addLocal(s.conversations[id!], "you", shown) } }));
        const body = files.length ? { text: trimmed, attachments: files.map((a) => a.path) } : { text: trimmed };
        if (files.length && !cmd) set({ attached: [] });
        const reply = await api.post<InputReply>(`/sessions/${id}/input`, body);
        if (reply.route) {
          const r = reply.route;
          if (r.where === "app") return appCommand(r.command);
          openScreen(r.screen, r.args);
        }
      } catch (e) {
        get().say(e instanceof Error ? e.message : String(e), "error");
      }

      function openScreen(name: string, args: string) {
        const { screen, page } = screenFor(name);
        if (page) set({ settingsPage: page });
        get().show(screen, args);
      }

      function appCommand(command: string) {
        if (command === "/theme") get().setTheme(get().theme === "dark" ? "light" : "dark");
        else if (command === "/help") {
          const lines = get()
            .commands.map((c) => `${c.name}  ${c.help}`)
            .join("\n");
          const keys = SHORTCUTS.map((k) => `${shortcutText(k.key, k.shift, isMac).padEnd(14)}${k.label}`).join("\n");
          const text = "Commands:\n\n```\n" + lines + "\n```\n\nShortcuts:\n\n```\n" + keys + "\n```";
          const id = get().current;
          if (id && get().conversations[id]) {
            set((s) => ({ conversations: { ...s.conversations, [id]: addLocal(s.conversations[id], "lead", text) } }));
          } else {
            // From the home screen: a new chat, opening with the list.
            void openSession({}, [{ key: "local-0", kind: "lead", text }]).catch((e: unknown) =>
              get().say(e instanceof Error ? e.message : String(e), "error"),
            );
          }
        }
      }
    },

    async answer(value, shown) {
      const { api, current, conversations } = get();
      if (!api || !current) return;
      const q = conversations[current]?.question;
      if (!q) return;
      get().say(""); // a note from the last turn, e.g. "Answer the question first…", has done its job
      const label = shown ?? (q.field.kind === "secret" ? "•".repeat(String(value).length) : String(value || "(Enter)"));
      if (get().welcome) {
        // The welcome is answered: the home screen gives way to set-up's conversation, which
        // starts at the top with the Lead's reply.
        set((s) => ({
          welcome: false,
          screen: "chat",
          conversations: { ...s.conversations, [current]: { ...s.conversations[current], lines: [] } },
        }));
      } else if (!(q.field.kind === "secret" && !value)) {
        set((s) => ({ conversations: { ...s.conversations, [current]: addLocal(s.conversations[current], "you", label) } }));
      }
      const files = typeof value === "string" && q.field.kind !== "secret" ? get().attached : [];
      try {
        await api.post(
          `/sessions/${current}/answer`,
          files.length ? { id: q.id, value, attachments: files.map((a) => a.path) } : { id: q.id, value },
        );
        if (files.length) set({ attached: [] });
      } catch (e) {
        get().say(e instanceof Error ? e.message : String(e), "error");
      }
    },

    async doneEditing() {
      // The drafts are saved (or left as they were): the interview reads them back.
      const { api, current, conversations } = get();
      const editing = current ? conversations[current]?.editing : null;
      if (!api || !current || !editing) return;
      try {
        if (get().remote) {
          // The drafts were edited as copies here: send each back before the interview reads it.
          const copies = get().copies;
          for (const path of editing.paths) {
            if (!copies[path]) continue; // never opened, so left as it was
            if (!inTauri) throw new Error("Editing by hand needs the Ombro app; choose Done without changes.");
            await api.putBytes(path, await readCopy(copies[path]));
          }
        }
      } catch (e) {
        get().say(`Your changes didn't reach the server: ${e instanceof Error ? e.message : String(e)}`, "error");
        return;
      }
      set((s) => ({ conversations: { ...s.conversations, [current]: addLocal(s.conversations[current], "you", "Done editing") } }));
      try {
        await api.post(`/sessions/${current}/answer`, { id: editing.id, value: null });
      } catch (e) {
        get().say(e instanceof Error ? e.message : String(e), "error");
      }
    },

    async openPath(path, how) {
      // Here: the file itself. From a server: a copy kept in ~/Downloads/Ombro, opened or shown.
      const { api, remote } = get();
      try {
        if (!remote) {
          await (how === "open" ? openFile(path) : revealFile(path));
          return;
        }
        if (!api) return;
        const copy = await saveCopy(api, path);
        set((s) => ({ copies: { ...s.copies, [path]: copy.saveAs } }));
        if (copy.local) await (how === "open" ? openFile(copy.local) : revealFile(copy.local));
      } catch (e) {
        get().say(e instanceof Error ? e.message : String(e), "error");
        throw e;
      }
    },

    async openFolder(path) {
      const { api, remote } = get();
      try {
        if (!remote) {
          await openLink("file://" + path.split("/").map(encodeURIComponent).join("/"));
          return;
        }
        if (!api) return;
        const saved = await saveFolder(api, path);
        if (saved.count === 0) get().say("That job made no files.");
        else if (saved.local) await openFile(saved.local);
      } catch (e) {
        get().say(e instanceof Error ? e.message : String(e), "error");
      }
    },

    async openHref(href) {
      if (href.startsWith("file:") && get().remote) {
        await get().openPath(decodeURIComponent(new URL(href).pathname), "open").catch(() => undefined);
        return;
      }
      await openLink(href);
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
        get().say(e instanceof Error ? e.message : String(e), "error");
      }
    },

    async deleteChat(name) {
      const { api, chatOn, screen } = get();
      if (!api) return;
      const open = Object.values(get().conversations).filter((c) => chatOf(c.id) === name).map((c) => c.id);
      const wasOn = chatOn === name || (screen === "chat" && open.includes(get().current ?? ""));
      // Off the list at once; the server's list replaces it when it answers.
      set((s) => ({ chats: s.chats.filter((c) => c.name !== name) }));
      try {
        const r = await api.post<{ moved_to: string; chats: ChatRow[] }>(`/chats/${encodeURIComponent(name)}/archive`);
        for (const id of open) delete chatNames[id];
        set({ chats: r.chats });
        if (wasOn) {
          get().say("");
          set({ screen: "home", current: null, chatOn: null, report: null, opener: null });
        }
      } catch (e) {
        void api.get<ChatRow[]>("/chats").then((chats) => set({ chats }), () => undefined);
        get().say(e instanceof Error ? e.message : String(e), "error");
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
      set({ theme, themeChoice: save ? theme : get().themeChoice });
      if (save) void get().api?.put("/preferences", { theme });
    },

    setThemeChoice(choice) {
      set({ themeChoice: choice, theme: choice === "auto" ? systemTheme() : choice });
      void get().api?.put("/preferences", { theme: choice });
    },

    showSettings(page) {
      set({ settingsPage: page, screen: "settings", screenArgs: "" });
    },

    async newMember(crew) {
      // A new crew member is an interview with the Lead, so it runs in a chat.
      try {
        await openSession({ flow: "role.add", crew });
      } catch (e) {
        get().say(e instanceof Error ? e.message : String(e), "error");
      }
    },

    toast(t) {
      const next: Toast = { tone: "info", dismissable: false, ...t };
      clearTimeout(toastTimers.get(next.id));
      toastTimers.delete(next.id);
      if (next.ms) toastTimers.set(next.id, setTimeout(() => get().dismissToast(next.id), next.ms));
      set((s) => ({ toasts: [...s.toasts.filter((x) => x.id !== next.id), next] }));
    },

    dismissToast(id) {
      clearTimeout(toastTimers.get(id));
      toastTimers.delete(id);
      if (get().toasts.some((x) => x.id === id)) set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) }));
    },

    say(text, tone = "info") {
      if (!text) get().dismissToast("notice");
      else get().toast({ id: "notice", text, tone, dismissable: true, ms: tone === "error" ? undefined : NOTICE_MS });
    },
  };
});

const toastTimers = new Map<string, ReturnType<typeof setTimeout>>();
const shownNotes = new Set<string>();

// Which saved chat each session shows, so picking it again in the sidebar returns to it.
const chatNames: Record<string, string> = {};
function chatOf(id: string): string | undefined {
  return chatNames[id];
}
