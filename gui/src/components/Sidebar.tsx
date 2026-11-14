// The sidebar: the wordmark, New, the chats, then Jobs, Approvals, Scheduled and Settings.

import { BriefcaseBusiness, CalendarClock, Check, PanelLeft, Plus, Settings } from "lucide-react";
import { useStore } from "../store/store";
import { Logo } from "./Logo";

export function Sidebar() {
  const { chats, counts, summary, screen, chatOn, goHome, openChat, show, toggleSidebar } = useStore();
  const c = counts ?? (summary
    ? { active: summary.counts.active_jobs, waiting: summary.counts.waiting, scheduled: summary.counts.scheduled_jobs }
    : null);
  return (
    <nav className="sidebar" aria-label="Ombro">
      <div className="sidebar-top" data-tauri-drag-region>
        <button className="icon-button" aria-label="Hide the sidebar" onClick={toggleSidebar}>
          <PanelLeft size={22} />
        </button>
      </div>
      <div className="logo-wrap" data-tauri-drag-region>
        <Logo size={27} />
      </div>
      <button className="nav new" onClick={goHome} aria-current={screen === "home" ? "page" : undefined}>
        <Plus size={22} /> New
      </button>
      <ul className="chats" aria-label="Chats">
        {chats.map((chat) => (
          <li key={chat.name}>
            <button
              className="chat-link"
              title={`${chat.title} (${chat.when})`}
              aria-current={screen === "chat" && chatOn === chat.name ? "page" : undefined}
              onClick={() => void openChat(chat.name)}
            >
              {chat.title}
            </button>
          </li>
        ))}
      </ul>
      <div className="sidebar-foot">
        <button className="nav" onClick={() => show("jobs")} aria-current={screen === "jobs" ? "page" : undefined}>
          <BriefcaseBusiness size={22} /> Jobs{c && c.active ? ` (${c.active})` : ""}
        </button>
        <button className="nav" onClick={() => show("approvals")} aria-current={screen === "approvals" ? "page" : undefined}>
          <Check size={22} /> Approvals{c ? ` (${c.waiting})` : ""}
        </button>
        <button className="nav" onClick={() => show("scheduled")} aria-current={screen === "scheduled" ? "page" : undefined}>
          <CalendarClock size={22} /> Scheduled{c ? ` (${c.scheduled})` : ""}
        </button>
        <button className="nav settings" onClick={() => show("settings")} aria-current={screen === "settings" ? "page" : undefined}>
          <Settings size={22} /> Settings
        </button>
      </div>
    </nav>
  );
}
