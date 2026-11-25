/**
 * Ombro avatar frames.
 *
 * A frame is 8 strings of 8 characters, top row first:
 *   "." off   "-" dim   "o" mid   "#" bright
 *
 * Layout used by the built in faces (keep to it so blink, wink and glance work):
 *   rows 0-1 brows, rows 2-5 eyes, rows 5-7 mouth
 *   left eye in columns 0-2, right eye in columns 5-7
 *   the pupil is the single "o" pixel inside each eye
 */

export type Mood = "positive" | "neutral" | "thinking" | "negative" | "other";

export interface FaceDef {
  name: string;
  mood: Mood;
  rows: readonly string[];
  levels: readonly number[];
}

const CHAR_LEVEL: Record<string, number> = { ".": 0, "-": 1, o: 2, "#": 3 };
const LEVEL_CHAR = ".-o#";

export function parseFrame(rows: readonly string[]): number[] {
  if (rows.length !== 8) throw new Error(`Ombro frame needs 8 rows, got ${rows.length}`);
  const out: number[] = [];
  rows.forEach((row, r) => {
    if (row.length !== 8) throw new Error(`Ombro frame row ${r} needs 8 characters: "${row}"`);
    for (const ch of row) {
      const v = CHAR_LEVEL[ch];
      if (v === undefined) throw new Error(`Ombro frame row ${r} has unknown character "${ch}"`);
      out.push(v);
    }
  });
  return out;
}

export function frameToRows(levels: ArrayLike<number>): string[] {
  const rows: string[] = [];
  for (let r = 0; r < 8; r++) {
    let s = "";
    for (let c = 0; c < 8; c++) s += LEVEL_CHAR[Math.max(0, Math.min(3, Math.round(levels[r * 8 + c])))];
    rows.push(s);
  }
  return rows;
}

const registry = new Map<string, FaceDef>();

/** Add or replace a face. Call this once at app start up to extend the set. */
export function registerFace(name: string, rows: readonly string[], mood: Mood = "other"): FaceDef {
  const def: FaceDef = { name, mood, rows: [...rows], levels: parseFrame(rows) };
  registry.set(name, def);
  return def;
}

export function getFace(name: string): FaceDef {
  return registry.get(name) ?? registry.get("calm")!;
}

export function hasFace(name: string): boolean {
  return registry.has(name);
}

export function listFaces(): FaceDef[] {
  return [...registry.values()];
}

export function facesByMood(...moods: Mood[]): string[] {
  return listFaces().filter((f) => moods.includes(f.mood)).map((f) => f.name);
}

/* Built in faces. The first three are from the original sketch. */
registerFace("calm", ["###..###", "##....##", "........", ".#o..o#.", "###..###", "##....##", "...##...", "........"], "positive");
registerFace("happy", ["###..###", "#......#", "........", ".#o..o#.", "###..###", "##....##", "..#..#..", "...##..."], "positive");
registerFace("laugh", ["o##..##o", "#......#", "........", ".#o..o#.", "###..###", "##.oo.##", "..####..", "...##..."], "positive");
registerFace("wink", ["###..###", "#......#", "........", ".#o.....", "###..###", "##......", "..#..#..", "...##..."], "positive");
registerFace("straight", ["........", "###..###", "........", ".#o..o#.", "###..###", "##....##", "..####..", "........"], "neutral");
registerFace("surprised", ["##....##", "........", "###..###", "#o#..#o#", "###..###", "...##...", "..#..#..", "...##..."], "neutral");
registerFace("concentrating", ["##....##", "..#..#..", "........", "........", "#o#..#o#", "........", "....##..", "........"], "thinking");
registerFace("askance", ["......##", "###..#..", "........", "........", "o##..o##", "###..###", "........", "...###.."], "thinking");
registerFace("sad", ["..#..#..", "##....##", "........", ".#o..o#.", "###..###", "##....##", "...##...", "..#..#.."], "negative");
registerFace("sleepy", ["....---.", ".....-..", "....---.", "........", "###..###", ".o....o.", "........", "...##..."], "other");
registerFace("dizzy", ["........", "........", "#.#..#.#", ".#....#.", "#.#..#.#", "........", "...#.#..", "..#.#.#."], "other");

/* Face modifiers used for idle life. They work on any face that follows the layout above. */

const LEFT_EYE = [0, 1, 2];
const RIGHT_EYE = [5, 6, 7];

function closeEye(l: number[], cols: number[]): void {
  const rows: number[] = [];
  for (let r = 2; r <= 5; r++) if (cols.some((c) => l[r * 8 + c] > 0)) rows.push(r);
  if (!rows.length) return;
  const mid = Math.floor((rows[0] + rows[rows.length - 1]) / 2);
  for (const r of rows) for (const c of cols) l[r * 8 + c] = 0;
  for (const c of cols) l[mid * 8 + c] = 3;
}

export function blinkFrame(src: readonly number[]): number[] {
  const l = [...src];
  closeEye(l, LEFT_EYE);
  closeEye(l, RIGHT_EYE);
  return l;
}

export function winkFrame(src: readonly number[]): number[] {
  const l = [...src];
  closeEye(l, RIGHT_EYE);
  return l;
}

/** Move each pupil one pixel in the given direction, where the eye allows it. */
export function glanceFrame(src: readonly number[], dx: number, dy: number): number[] {
  const l = [...src];
  for (const cols of [LEFT_EYE, RIGHT_EYE]) {
    let done = false;
    for (let r = 2; r <= 5 && !done; r++) {
      for (const c of cols) {
        if (l[r * 8 + c] !== 2) continue;
        for (const yy of dy ? [dy, 0] : [0]) {
          const nr = r + yy, nc = c + dx;
          if (nr < 2 || nr > 5 || !cols.includes(nc)) continue;
          const j = nr * 8 + nc;
          if (l[j] === 3) { l[j] = 2; l[r * 8 + c] = 3; break; }
        }
        done = true;
        break;
      }
    }
  }
  return l;
}
