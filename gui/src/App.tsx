// The window: sidebar, main column (title bar, page, foot). The page is the home screen, a
// conversation, or one of the pages to come.

import { useEffect } from "react";
import { PanelLeft, PanelRight } from "lucide-react";
import { Composer } from "./components/Composer";
import { Sidebar } from "./components/Sidebar";
import { Conversation } from "./screens/Conversation";
import { Home, HomeGreeting } from "./screens/Home";
import { Placeholder } from "./screens/Placeholder";
import { isMac } from "./platform";
import { useStore } from "./store/store";

export default function App() {
  const s = useStore();
  const { status, problem, screen, current, conversations, sidebar, theme, titles } = s;

  useEffect(() => {
    void useStore.getState().start();
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  if (status === "starting" && !s.summary) {
    return <div className="startup">Starting Ombro…</div>;
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
  const inChat = screen === "chat" && conversation;
  const title =
    screen === "chat"
      ? (current && titles[current]) || (conversation?.kind === "setup" ? "Setting up Ombro" : "New chat")
      : "";

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
          <button className="icon-button" aria-label="Show the inspector" title="The inspector comes in the next stage" disabled>
            <PanelRight size={22} />
          </button>
        </div>
        {screen === "home" && <Home />}
        {inChat && <Conversation conversation={conversation} />}
        {!inChat && screen !== "home" && <Placeholder screen={screen} args={s.screenArgs} />}
        {(screen === "home" || inChat) && (
          <>
            {screen === "home" && <HomeGreeting />}
            <Composer conversation={inChat ? conversation : null} />
          </>
        )}
        {status === "lost" && <div className="notice column">Reconnecting to Ombro…</div>}
      </main>
    </div>
  );
}
