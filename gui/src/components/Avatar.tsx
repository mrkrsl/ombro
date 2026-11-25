// The Lead's avatar, as the app uses it: beside each turn of the Lead's in the chat, and in the
// thinking line above the input. Only one moves at a time: the thinking one while the Lead works,
// else the newest reply's (its intro once, then a blink now and then). Every other is still.

import { useEffect, useRef } from "react";
import { OmbroAvatar, type Palette } from "./ombro-avatar";

/** Sizes in px: the panel, not counting its shadow. */
export const REPLY_SIZE = 28;
export const THINKING_SIZE = 24;

/** The panel and its shadow in the Ombro blues (tokens.css); the pixels keep the library's ramp. */
const PALETTE: Partial<Palette> = { panel: "#031216", shadow: "#93c4d0" };

const INTROS = ["fireworks", "target", "stars"];

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
 * intro (the first time only) and then blink; otherwise it shows its face, still. A line from a
 * reopened chat's past is never moving. */
export function ReplyAvatar({ id, seed, moving }: { id: string; seed: string; moving: boolean }) {
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
    <OmbroAvatar
      className="reply-avatar"
      size={REPLY_SIZE}
      seed={seed}
      mode={moving ? "message" : "static"}
      animateIn={moving && !!intro.current}
      intro={INTROS}
      palette={PALETTE}
      label="Ombro Lead"
    />
  );
}

/** The thinking line's: faces, the spinner, effects, and the chat's title scrolling across. */
export function ThinkingAvatar({ title }: { title: string }) {
  const words = scrollable(title);
  return (
    <OmbroAvatar
      size={THINKING_SIZE}
      mode="thinking"
      intro={null}
      face="calm"
      palette={PALETTE}
      think={{ texts: words ? [words, "THINKING", "WORKING"] : ["THINKING", "WORKING"] }}
      label="The Lead is thinking"
    />
  );
}
