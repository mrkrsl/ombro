/**
 * Your own faces and effects go here. This file is loaded by the component
 * and by the icon export script, so anything added here works in both.
 *
 * Frame characters: "." off, "-" dim, "o" mid, "#" bright.
 * Mood decides when a face is picked automatically:
 *   positive  message avatars
 *   thinking, neutral  the thinking area
 *   negative, other  only when asked for by name
 */
import { registerFace } from "./frames";
// import { registerEffect } from "./effects";

registerFace(
  "cheeky",
  [
    "###..###",
    "#......#",
    "........",
    "o#....#o",
    "###..###",
    "##....##",
    "....#...",
    "...##...",
  ],
  "positive",
);

// registerEffect("blink-all", () => ({
//   step: 120,
//   maxMs: 1200,
//   frame(out, t) {
//     out.fill(Math.floor(t / 120) % 2 ? 3 : 0);
//     return false;
//   },
// }));
