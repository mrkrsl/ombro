// The Lead's avatar, as the app uses it: beside each turn of the Lead's in the chat, in the
// thinking line above the input, and (small, with no panel) beside a chat with a job running.
// While the Lead works, only the thinking one moves. Otherwise the newest reply plays its intro
// once, then blinks, winks or glances every few seconds; an older one, every 30 to 60 seconds, does
// a little thing and settles back, and never while another is.

import { useEffect, useRef } from "react";
import { OmbroAvatar, facesByMood, type OmbroAvatarHandle, type Palette } from "./ombro-avatar";
import { useStore, type Theme } from "../store/store";

/** Sizes in px: the panel, not counting its shadow. */
export const REPLY_SIZE = 28;
export const THINKING_SIZE = 24;
export const JOB_SIZE = 15;
export const HOME_SIZE = 168;

/** In the Ombro blues (tokens.css), as the logo: in light mode a dark panel with light pixels (the
 * mid ones brighter than the library's, which read dark at these sizes); in dark mode the other
 * way round, a light panel with dark pixels, as the dark mode logo, its shadow the 500 blue (black
 * can't be told from the background at this size). */
const PALETTES: Record<Theme, Partial<Palette>> = {
  light: { panel: "#031216", shadow: "#93c4d0", mid: "#93c4d0" },
  dark: { panel: "#c7e3eb", shadow: "#205360", off: "#afd4de", dim: "#7fb0bf", mid: "#3f8395", on: "#031216" },
};

/** The sidebar's: no panel, so the pixels sit on the sidebar itself; the unlit ones a faint grid,
 * so it never disappears between effects. */
const JOB_PALETTES: Record<Theme, Partial<Palette>> = {
  light: { panel: "transparent", off: "#dbe8ec", dim: "#93c4d0", mid: "#3f8395", on: "#031216" },
  dark: { panel: "transparent", off: "#1d3a42", dim: "#3f8395", mid: "#93c4d0", on: "#ebf5f7" },
};

const usePalette = (set: Record<Theme, Partial<Palette>>) => set[useStore((s) => s.theme)];

const INTROS = ["fireworks", "target", "stars"];

/** The newest reply's gestures: more often than the library's, to say it's still there. */
const NEWEST_IDLE = { minMs: 3000, maxMs: 7000 };

/** An older reply's little thing: every 30 to 60 seconds. */
const NOW_AND_THEN: [number, number] = [30_000, 60_000];
const LITTLE_THINGS = ["blink", "wink", "glance", "stars", "target", "fireworks"] as const;

/** An older reply doing its little thing: no other starts one meanwhile. */
let oneMoving = false;

const between = ([a, b]: [number, number]) => a + Math.random() * (b - a);

function onScreen(el: Element | null): boolean {
  if (!el) return false;
  const r = el.getBoundingClientRect();
  return r.bottom > 0 && r.top < window.innerHeight && r.width > 0;
}

/** Now and then, while `on`: a blink, wink or glance, or an effect and back to its face. */
function useNowAndThen(on: boolean, avatar: React.RefObject<OmbroAvatarHandle | null>, box: React.RefObject<HTMLElement | null>) {
  useEffect(() => {
    if (!on) return;
    let timer: ReturnType<typeof setTimeout>;
    let live = true;
    const next = () => {
      timer = setTimeout(async () => {
        const a = avatar.current;
        const c = a?.controller;
        if (a && c && !oneMoving && onScreen(box.current)) {
          oneMoving = true;
          try {
            const what = LITTLE_THINGS[Math.floor(Math.random() * LITTLE_THINGS.length)];
            if (what === "blink") await a.blink();
            else if (what === "wink") await a.wink();
            else if (what === "glance") await c.glance();
            else {
              const face = c.face;
              await a.play(what);
              if (live) c.setFace(face, 450);
            }
          } finally {
            oneMoving = false;
          }
        }
        if (live) next();
      }, between(NOW_AND_THEN));
    };
    next();
    return () => {
      live = false;
      clearTimeout(timer);
      avatar.current?.controller?.snapFace(avatar.current.controller.face);
    };
  }, [on, avatar, box]);
}

/** Replies whose intro has played, so coming back to a chat doesn't play them again. */
const played = new Set<string>();

/** A chat title as the avatar's 3×5 font can show it: capitals, digits and a little punctuation,
 * accents dropped, anything else a space. */
export function scrollable(title: string): string {
  return title
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toUpperCase()
    .replace(/[‘’]/g, "'")
    .replace(/[–—]/g, "-")
    .replace(/[^A-Z0-9 !?.:,'-]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Beside a turn of the Lead's. `id` names the line in this window (the chat and the line's key);
 * `seed` picks its face, and is the reply's words, so a reopened chat shows each reply with the face
 * it had (its lines come back under new keys). `moving`: this is the newest reply and the Lead isn't working, so it may play its
 * intro (the first time only) and then blink often; otherwise it shows its face, and does a little
 * thing now and then unless `quiet` (the Lead is working). A line from a
 * reopened chat's past is never moving. */
export function ReplyAvatar({ id, seed, moving, quiet = false }: { id: string; seed: string; moving: boolean; quiet?: boolean }) {
  const avatar = useRef<OmbroAvatarHandle>(null);
  const box = useRef<HTMLSpanElement>(null);
  const palette = usePalette(PALETTES);
  useNowAndThen(!moving && !quiet, avatar, box);
  // Decided once, when it first may move: a later re-render must not cut the intro short.
  const intro = useRef<boolean | null>(null);
  if (moving && intro.current === null) intro.current = !played.has(id);
  useEffect(() => {
    if (!moving) return;
    played.add(id);
    return () => {
      intro.current = false; // stilled once: when it may move again, it only blinks
    };
  }, [moving, id]);
  return (
    <span className="reply-avatar" ref={box}>
      <OmbroAvatar
        ref={avatar}
        size={REPLY_SIZE}
        seed={seed}
        mode={moving ? "message" : "static"}
        animateIn={moving && !!intro.current}
        intro={INTROS}
        idle={NEWEST_IDLE}
        palette={palette}
        label="Ombro Lead"
      />
    </span>
  );
}

/** Words for the Lead thinking, scrolled in turn with the chat's title. */
const THINKING_WORDS = ["THINKING", "PONDERING", "MULLING IT OVER", "JOINING THE DOTS", "WEIGHING IT UP"];
/** And for a job of the chat's running in the background. */
const BACKGROUND_WORDS = ["WORKING", "ON IT", "IN THE BACKGROUND"];

/** The words to scroll, with the chat's title among them. */
function wordsFor(title: string, background: boolean): string[] {
  const words = scrollable(title);
  const texts: string[] = [];
  (background ? BACKGROUND_WORDS : THINKING_WORDS).forEach((w, i) => {
    if (words && i % 2 === 0) texts.push(words);
    texts.push(w);
  });
  return texts;
}

/** The thinking line's: faces, the spinner, effects, and words scrolling across, the chat's title
 * every third. `background`: the Lead isn't thinking, but a job from this chat is running. */
export function ThinkingAvatar({ title, background = false }: { title: string; background?: boolean }) {
  const palette = usePalette(PALETTES);
  const texts = wordsFor(title, background);
  return (
    <OmbroAvatar
      size={THINKING_SIZE}
      mode="thinking"
      intro={null}
      face="calm"
      palette={palette}
      think={{ texts }}
      label={background ? "A job from this chat is working in the background" : "The Lead is thinking"}
    />
  );
}

/** Beside a chat with a job running: as the thinking line's while a job works in the background
 * (faces, effects, the chat's title), small, square and with no panel. Each runs on its own
 * timings, so several in the list soon fall out of step. */
export function JobAvatar({ title }: { title: string }) {
  const palette = usePalette(JOB_PALETTES);
  return (
    <OmbroAvatar
      className="job-avatar"
      size={JOB_SIZE}
      mode="thinking"
      intro={null}
      face="calm"
      shadow={false}
      skew={false}
      palette={palette}
      think={{ texts: wordsFor(title, true) }}
      label="A job from this chat is running"
    />
  );
}

/** What the home screen's avatar says now and then, in turn. */
const HOME_WORDS = ["OMBRO", "READY WHEN YOU ARE", "GO!", "PUT A TEAM ON IT", "I'M THE NUMBER TWO YOU"];

/** The home screen's, in the wordmark's place: large, walking through faces of every kind, the
 * effects and its words, from a random point. */
export function HomeAvatar() {
  const palette = usePalette(PALETTES);
  return (
    <OmbroAvatar
      className="home-avatar"
      size={HOME_SIZE}
      mode="thinking"
      intro={null}
      face="happy"
      palette={palette}
      think={{ faces: facesByMood("positive", "neutral", "thinking"), texts: HOME_WORDS }}
      label="Ombro"
    />
  );
}
