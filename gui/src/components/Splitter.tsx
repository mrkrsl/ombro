// Resizing the sidebar and the side panel by dragging the line beside them (below the title bar,
// which drags the window). Sizes are kept on this computer; a double-click puts one back.

import { useRef, type PointerEvent } from "react";

const SIZES_KEY = "ombro.panel-sizes";

export const SIDEBAR_DEFAULT = 258;
export const SIDEBAR_MIN = 220;
export const SIDEBAR_MAX = 400;
export const PANEL_DEFAULT = 0.5; // the side panel's share of the width beside the sidebar
export const PANEL_MIN = 380; // px
export const CHAT_MIN = 420; // px: the chat keeps at least this

export type Sizes = { sidebar: number; panel: number };

export function keptSizes(): Sizes {
  try {
    const v = JSON.parse(localStorage.getItem(SIZES_KEY) ?? "{}") as Partial<Sizes>;
    return {
      sidebar: clampSidebar(Number(v.sidebar) || SIDEBAR_DEFAULT),
      panel: Number(v.panel) > 0 && Number(v.panel) < 1 ? Number(v.panel) : PANEL_DEFAULT,
    };
  } catch {
    return { sidebar: SIDEBAR_DEFAULT, panel: PANEL_DEFAULT };
  }
}

export function keepSizes(sizes: Sizes): void {
  try {
    localStorage.setItem(SIZES_KEY, JSON.stringify(sizes));
  } catch {
    // no storage: the sizes last until the window closes
  }
}

export function clampSidebar(px: number): number {
  return Math.round(Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, px)));
}

/** The side panel's share, from where its left edge is dragged to: `room` is the width beside the
 * sidebar, `right` how far the edge is from the window's right side. */
export function panelShare(right: number, room: number): number {
  if (room <= 0) return PANEL_DEFAULT;
  const px = Math.min(room - CHAT_MIN, Math.max(PANEL_MIN, right));
  return Math.min(0.9, Math.max(0.1, px / room));
}

/** The side panel's width as CSS: its share of the room beside the sidebar, never under PANEL_MIN,
 * and never so wide the chat has less than CHAT_MIN. */
export function panelWidth(share: number, sidebar: number): string {
  const room = `(100vw - ${sidebar}px)`;
  return `clamp(${PANEL_MIN}px, calc(${room} * ${share}), max(${PANEL_MIN}px, calc(${room} - ${CHAT_MIN}px)))`;
}

/** A line to drag. `onDrag` gets the pointer's x as it moves; `onDone` when it's let go; `onReset`
 * on a double-click. */
export function Splitter({ side, label, onDrag, onDone, onReset }: {
  side: "left" | "right";
  label: string;
  onDrag: (x: number) => void;
  onDone: () => void;
  onReset: () => void;
}) {
  const dragging = useRef(false);
  const down = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    dragging.current = true;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    document.body.classList.add("resizing");
  };
  const move = (e: PointerEvent<HTMLDivElement>) => {
    if (dragging.current) onDrag(e.clientX);
  };
  const up = (e: PointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return;
    dragging.current = false;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    document.body.classList.remove("resizing");
    onDone();
  };
  return (
    <div
      className={`splitter ${side}`}
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      title="Drag to resize; double-click to reset"
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={up}
      onDoubleClick={onReset}
    />
  );
}
