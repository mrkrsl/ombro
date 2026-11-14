// Settings (designs 09–16): its own sub-navigation beside the page. /preferences, /crew, /folder,
// /tools and /skills open it at their page.

import { BadgeCheck, CircleUserRound, Folder, UserRoundGroup, Wrench } from "lucide-react";
import type { ReactElement } from "react";
import { useStore, type SettingsPage } from "../../store/store";
import { Crews } from "./Crews";
import { Folders } from "./Folders";
import { General } from "./General";
import { Skills } from "./Skills";
import { Tools } from "./Tools";

const PAGES: [SettingsPage, string, ReactElement][] = [
  ["general", "General", <CircleUserRound key="g" size={22} />],
  ["crews", "Crews", <UserRoundGroup key="c" size={22} />],
  ["folders", "Folders", <Folder key="f" size={22} />],
  ["tools", "Tools", <Wrench key="t" size={22} />],
  ["skills", "Skills", <BadgeCheck key="s" size={22} />],
];

export function Settings() {
  const { settingsPage, showSettings } = useStore();
  const page: Record<SettingsPage, ReactElement> = {
    general: <General />,
    crews: <Crews />,
    folders: <Folders />,
    tools: <Tools />,
    skills: <Skills />,
  };
  return (
    <div className="page">
      <div className="settings-screen">
        <nav className="settings-nav" aria-label="Settings">
          {PAGES.map(([id, label, icon]) => (
            <button
              key={id}
              className="settings-link"
              aria-current={settingsPage === id ? "page" : undefined}
              onClick={() => showSettings(id)}
            >
              {icon} {label}
            </button>
          ))}
        </nav>
        <div className="settings-page" key={settingsPage}>
          {page[settingsPage]}
        </div>
      </div>
    </div>
  );
}
