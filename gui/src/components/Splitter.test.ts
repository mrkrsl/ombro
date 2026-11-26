// Resizing: the sidebar between its limits, the side panel as a share that leaves the chat room.
import { describe, expect, it } from "vitest";
import { CHAT_MIN, clampSidebar, PANEL_MIN, panelShare, panelWidth, SIDEBAR_MAX, SIDEBAR_MIN } from "./Splitter";

describe("panel sizes", () => {
  it("keeps the sidebar between its limits", () => {
    expect(clampSidebar(100)).toBe(SIDEBAR_MIN);
    expect(clampSidebar(300.4)).toBe(300);
    expect(clampSidebar(900)).toBe(SIDEBAR_MAX);
  });

  it("keeps the side panel wide enough, and the chat too", () => {
    expect(panelShare(600, 1200)).toBe(0.5);
    expect(panelShare(100, 1200)).toBe(PANEL_MIN / 1200);
    expect(panelShare(1150, 1200)).toBe((1200 - CHAT_MIN) / 1200);
  });

  it("writes the panel's width as CSS with both limits", () => {
    expect(panelWidth(0.5, 258)).toBe(
      `clamp(${PANEL_MIN}px, calc((100vw - 258px) * 0.5), max(${PANEL_MIN}px, calc((100vw - 258px) - ${CHAT_MIN}px)))`,
    );
  });
});
