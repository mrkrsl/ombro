// The sidebar: the wordmark, New, the chats, then Jobs, Approvals, Scheduled and Settings.

import { BriefcaseBusiness, CalendarClock, Check, CircleDashed, PanelLeft, Plus, Settings, X } from "lucide-react";
import { useStore } from "../store/store";
import { Logo } from "./Logo";

export function Sidebar() {
  const { chats, counts, summary, screen, chatOn, current, goHome, openChat, deleteChat, show, toggleSidebar, resumeSetup, running } =
    useStore();
  // Set-up in progress, to come back to from a page (not while its welcome waits on the home screen).
  const setup = useStore((s) =>
    s.welcome
      ? undefined
      : Object.values(s.conversations).find((c) => c.kind === "setup" && (c.state === "working" || c.state === "waiting")),
  );
  const onSetup = !!setup && current === setup.id && (screen === "chat" || screen === "home");
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
        {setup && (
          <li>
            <button className="chat-link" aria-current={onSetup ? "page" : undefined} onClick={resumeSetup}>
              Setting up Ombro
            </button>
          </li>
        )}
        {chats.map((chat) => (
          <li key={chat.name} className="chat-row">
            <button
              className="chat-link"
              title={`${chat.title} (${chat.when})`}
              aria-current={screen === "chat" && chatOn === chat.name ? "page" : undefined}
              onClick={() => void openChat(chat.name)}
            >
              {chat.jobs.some((id) => running.includes(id)) && (
                <CircleDashed className="working" size={15} aria-label="A job from this chat is running" />
              )}
              {chat.title}
            </button>
            {/* Shown on hover only, so the title reads in full until then. No confirmation: the chat
                moves to the chats folder's Archive/, kept as it was. */}
            <button
              className="icon-button chat-delete"
              aria-label="Delete this chat"
              title="Delete this chat (it's kept in Chats/Archive)"
              onClick={() => void deleteChat(chat.name)}
            >
              <X size={15} />
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
