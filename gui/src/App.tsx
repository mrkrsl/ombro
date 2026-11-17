// The window: sidebar, main column (title bar, page, foot). The page is the home screen, a
// conversation, or one of the pages to come.

import { useEffect, type ReactElement } from "react";
import { PanelLeft, PanelRight } from "lucide-react";
import { Composer } from "./components/Composer";
import { Sidebar } from "./components/Sidebar";
import { Conversation } from "./screens/Conversation";
import { Home, HomeGreeting } from "./screens/Home";
import { Inspector } from "./components/Inspector";
import { Approvals } from "./screens/Approvals";
import { Connect } from "./screens/Connect";
import { Jobs } from "./screens/Jobs";
import { Placeholder } from "./screens/Placeholder";
import { ReportPage } from "./screens/ReportPage";
import { Scheduled } from "./screens/Scheduled";
import { Settings } from "./screens/settings/Settings";
import { isMac, onTrayOpen, setTray, startZoom } from "./platform";
import { shortcutFor } from "./shortcuts";
import { useStore } from "./store/store";

export default function App() {
  const s = useStore();
  const { status, problem, screen, current, conversations, sidebar, theme, titles, inspector } = s;

  useEffect(() => {
    void useStore.getState().start();
    void onTrayOpen((screen) => {
      const st = useStore.getState();
      // View › Chat: the chat on screen last, or the home screen when there is none.
      if (screen === "home" || (screen === "chat" && !st.current)) st.goHome();
      else if (screen !== "current") st.show(screen as Parameters<typeof st.show>[0]);
    });
  }, []);

  // The menu bar item shows the same counts as the sidebar.
  const counts = s.counts;
  useEffect(() => {
    if (counts)
      void setTray({ active: counts.active, scheduled: counts.scheduled, waiting: counts.waiting, unseen: counts.unseen, crews_ready: counts.crews_ready });
  }, [counts]);

  useEffect(() => startZoom(), []);

  // Keyboard shortcuts (shortcuts.ts): Cmd on macOS, Ctrl elsewhere.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const action = shortcutFor(e, isMac);
      if (!action) return;
      e.preventDefault();
      const st = useStore.getState();
      if (action === "new") st.goHome();
      else if (action === "sidebar") st.toggleSidebar();
      else if (action === "inspector") st.toggleInspector();
      else if (action === "settings") st.showSettings("general");
      else if (action === "commands") {
        // The / menu, in the input: from a page, the chat or home screen comes back first.
        if (st.screen !== "home" && st.screen !== "chat") st.current ? st.show("chat") : st.goHome();
        setTimeout(() => window.dispatchEvent(new Event("ombro:commands")), 0);
      } else st.show(action);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  // "Same as the system": follow it when it changes.
  const themeChoice = s.themeChoice;
  useEffect(() => {
    if (themeChoice !== "auto" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const follow = () => useStore.setState({ theme: mq.matches ? "dark" : "light" });
    mq.addEventListener?.("change", follow);
    return () => mq.removeEventListener?.("change", follow);
  }, [themeChoice]);

  if (status === "choose") {
    return <Connect onReady={() => void s.connectNow()} />;
  }
  if (status === "starting" && !s.summary) {
    return <div className="startup">{s.where && s.where.mode !== "local" ? "Connecting to the server…" : "Starting Ombro…"}</div>;
  }
  if (status === "failed" && s.where && (s.where.mode === "ssh" || s.where.mode === "direct")) {
    return <Connect problem={problem} onReady={() => void s.connectNow()} />;
  }
  if (status === "failed") {
    return (
      <div className="startup">
        <div>
          <p>Ombro could not start.</p>
          <p>{problem}</p>
        </div>
      </div>
    );
  }

  const conversation = current ? conversations[current] ?? null : null;
  // The inspector is about a chat: on the pages (Jobs, Approvals, Scheduled, Settings, a report)
  // it and its button are hidden.
  const chatScreen = screen === "home" || screen === "chat";
  const showInspector = inspector && chatScreen;
  const inChat = screen === "chat" && conversation;
  const PAGE_TITLES: Record<string, string> = {
    jobs: "Jobs", approvals: "Approvals", scheduled: "Scheduled work", settings: "Settings", report: "Report",
  };
  const title =
    screen === "chat"
      ? (current && titles[current]) || (conversation?.kind === "setup" ? "Setting up Ombro" : "New chat")
      : PAGE_TITLES[screen] ?? "";
  const pages: Record<string, ReactElement> = {
    jobs: <Jobs />, approvals: <Approvals />, scheduled: <Scheduled />, report: <ReportPage />, settings: <Settings />,
  };

  return (
    <div className={`window ${isMac ? "mac" : "linux"}`}>
      {sidebar && <Sidebar />}
      <main className="main">
        <div className={`main-top ${sidebar ? "" : "wide"}`} data-tauri-drag-region>
          {!sidebar && (
            <button className="icon-button" aria-label="Show the sidebar" onClick={s.toggleSidebar}>
              <PanelLeft size={22} />
            </button>
          )}
          <div className="title" data-tauri-drag-region>
            {title}
          </div>
          {chatScreen && !inspector && (
            <button className="icon-button" aria-label="Show the inspector" onClick={s.toggleInspector}>
              <PanelRight size={22} />
            </button>
          )}
        </div>
        {screen === "home" && <Home />}
        {inChat && <Conversation conversation={conversation} />}
        {!inChat && screen !== "home" && (pages[screen] ?? <Placeholder screen={screen} args={s.screenArgs} />)}
        {screen === "home" && <HomeGreeting />}
        {chatScreen && <Composer conversation={inChat || (screen === "home" && s.welcome) ? conversation : null} />}
        {/* On a page there is no chat box to show a notice under, so it shows here. */}
        {!chatScreen && s.notice && <div className="notice column" role="status">{s.notice}</div>}
        {status === "lost" && <div className="notice column">{s.remote ? "Reconnecting to the server…" : "Reconnecting to Ombro…"}</div>}
      </main>
      {showInspector && (
        <div className="inspector-wrap">
          <div className="inspector-top" data-tauri-drag-region>
            <button className="icon-button" aria-label="Hide the inspector" onClick={s.toggleInspector}>
              <PanelRight size={22} />
            </button>
          </div>
          <Inspector />
        </div>
      )}
    </div>
  );
}
