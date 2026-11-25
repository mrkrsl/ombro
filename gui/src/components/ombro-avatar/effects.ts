/**
 * Effects are short animations that draw straight into the 8x8 grid.
 * Each call to an effect factory returns a fresh run with its own state.
 * frame() fills `out` (64 levels, 0 to 3, fractions allowed) and returns true when finished.
 */

export interface EffectRun {
  /** milliseconds between frames */
  step: number;
  /** hard stop in milliseconds */
  maxMs: number;
  /** how quickly pixels ease, in ms. Lower is snappier. */
  tau?: number;
  frame(out: Float32Array, t: number): boolean;
}

export type EffectFactory = () => EffectRun;

const rand = (a: number, b: number) => a + Math.random() * (b - a);

function put(out: Float32Array, y: number, x: number, v: number) {
  const r = Math.round(y), c = Math.round(x);
  if (r < 0 || r > 7 || c < 0 || c > 7) return;
  const i = r * 8 + c;
  if (v > out[i]) out[i] = v;
}

function ring(o: number): [number, number][] {
  const p: [number, number][] = [];
  for (let c = o; c <= 7 - o; c++) p.push([o, c]);
  for (let r = o + 1; r <= 7 - o; r++) p.push([r, 7 - o]);
  for (let c = 6 - o; c >= o; c--) p.push([7 - o, c]);
  for (let r = 6 - o; r > o; r--) p.push([r, o]);
  return p;
}
const OUTER = ring(0);
const INNER = ring(1);

export const fireworks: EffectFactory = () => {
  type P = { x: number; y: number; vx: number; vy: number; l: number };
  const parts: P[] = [];
  const rockets = [0, 650, 1250].map((a) => ({
    at: a + rand(0, 150), c: 1 + Math.floor(rand(0, 6)), y: 8, burst: 1 + Math.floor(rand(0, 3)), boom: false,
  }));
  return {
    step: 55, maxMs: 5000, tau: 30,
    frame(out, t) {
      for (const r of rockets) {
        if (t < r.at || r.boom) continue;
        r.y -= 0.8;
        if (r.y <= r.burst) {
          r.boom = true;
          const n = 10, off = rand(0, 1);
          for (let j = 0; j < n; j++) {
            const an = (j / n) * Math.PI * 2 + off, sp = rand(0.4, 0.7);
            parts.push({ x: r.c, y: r.burst, vx: Math.cos(an) * sp, vy: Math.sin(an) * sp, l: 1 });
          }
          put(out, r.burst, r.c, 3);
        } else {
          put(out, r.y, r.c, 3);
          put(out, r.y + 1, r.c, 1.2);
        }
      }
      for (const p of parts) {
        if (p.l <= 0) continue;
        p.x += p.vx; p.y += p.vy; p.vy += 0.045; p.l -= 0.07;
        if (p.l > 0) put(out, p.y, p.x, 3 * Math.min(1, p.l * 1.3));
      }
      return rockets.every((r) => r.boom) && parts.every((p) => p.l <= 0);
    },
  };
};

/** Rings rippling outward from the centre. */
export const target: EffectFactory = () => ({
  step: 100, maxMs: 2000, tau: 30,
  frame(out, t) {
    const ph = Math.floor(t / 100);
    for (let r = 0; r < 8; r++)
      for (let c = 0; c < 8; c++) {
        const k = Math.floor(Math.max(Math.abs(r - 3.5), Math.abs(c - 3.5)));
        out[r * 8 + c] = [3, 2, 1, 0, 0][(((ph - k) % 5) + 5) % 5];
      }
    return false;
  },
});

export const stars: EffectFactory = () => {
  const a = new Float32Array(64);
  return {
    step: 80, maxMs: 2600, tau: 30,
    frame(out) {
      for (let i = 0; i < 64; i++) a[i] *= 0.8;
      if (Math.random() < 0.75) {
        const r = Math.floor(rand(0, 8)), c = Math.floor(rand(0, 8));
        a[r * 8 + c] = 3;
        if (Math.random() < 0.4)
          for (const [dy, dx] of [[0, 1], [0, -1], [1, 0], [-1, 0]]) {
            const y = r + dy, x = c + dx;
            if (y >= 0 && y < 8 && x >= 0 && x < 8) a[y * 8 + x] = Math.max(a[y * 8 + x], 1.3);
          }
      }
      out.set(a);
      return false;
    },
  };
};

/** A comet running round the edge with a counter rotating inner ring. */
export const spinner = (ms = 2400): EffectRun => ({
  step: 55, maxMs: ms, tau: 40,
  frame(out, t) {
    const h = Math.floor(t / 55);
    [3, 2.2, 1.4, 0.7].forEach((v, j) => { const [r, c] = OUTER[(((h - j) % 28) + 28) % 28]; out[r * 8 + c] = v; });
    const h2 = Math.floor(t / 90);
    [2, 1].forEach((v, j) => { const [r, c] = INNER[(((-h2 + j) % 20) + 20) % 20]; out[r * 8 + c] = v; });
    const pulse = 1 + Math.sin(t / 200);
    for (const i of [27, 28, 35, 36]) out[i] = pulse;
    return false;
  },
});

/* 3x5 pixel font. Rows top to bottom, separated by "|". */
const FONT: Record<string, string> = {
  A: ".#.|#.#|###|#.#|#.#", B: "##.|#.#|##.|#.#|##.", C: ".##|#..|#..|#..|.##", D: "##.|#.#|#.#|#.#|##.",
  E: "###|#..|##.|#..|###", F: "###|#..|##.|#..|#..", G: ".##|#..|#.#|#.#|.##", H: "#.#|#.#|###|#.#|#.#",
  I: "###|.#.|.#.|.#.|###", J: "..#|..#|..#|#.#|.#.", K: "#.#|#.#|##.|#.#|#.#", L: "#..|#..|#..|#..|###",
  M: "#...#|##.##|#.#.#|#...#|#...#", N: "#..#|##.#|#.##|#..#|#..#", O: ".#.|#.#|#.#|#.#|.#.",
  P: "##.|#.#|##.|#..|#..", Q: ".#.|#.#|#.#|##.|.##", R: "##.|#.#|##.|#.#|#.#", S: ".##|#..|.#.|..#|##.",
  T: "###|.#.|.#.|.#.|.#.", U: "#.#|#.#|#.#|#.#|###", V: "#.#|#.#|#.#|#.#|.#.", W: "#...#|#...#|#.#.#|##.##|#...#",
  X: "#.#|#.#|.#.|#.#|#.#", Y: "#.#|#.#|.#.|.#.|.#.", Z: "###|..#|.#.|#..|###",
  "0": "###|#.#|#.#|#.#|###", "1": ".#.|##.|.#.|.#.|###", "2": "##.|..#|.#.|#..|###", "3": "##.|..#|.#.|..#|##.",
  "4": "#.#|#.#|###|..#|..#", "5": "###|#..|##.|..#|##.", "6": ".##|#..|###|#.#|###", "7": "###|..#|.#.|.#.|.#.",
  "8": "###|#.#|###|#.#|###", "9": "###|#.#|###|..#|##.",
  " ": "..|..|..|..|..", "!": "#|#|#|.|#", "?": "##.|..#|.#.|...|.#.", ".": ".|.|.|.|#", ":": ".|#|.|#|.",
  ",": "..|..|..|.#|#.", "'": "#|#|.|.|.", "-": "...|...|###|...|...",
};

/** Scroll text right to left across rows 1 to 5. */
export const scrollText = (text: string, msPerColumn = 80): EffectRun => {
  const cols: number[] = Array(8).fill(0);
  for (const ch of text.toUpperCase()) {
    const g = (FONT[ch] ?? FONT["?"]).split("|");
    for (let x = 0; x < g[0].length; x++) {
      let m = 0;
      g.forEach((row, y) => { if (row[x] === "#") m |= 1 << y; });
      cols.push(m);
    }
    cols.push(0);
  }
  for (let i = 0; i < 8; i++) cols.push(0);
  return {
    step: msPerColumn, maxMs: cols.length * msPerColumn + 500, tau: 22,
    frame(out, t) {
      const off = Math.floor(t / msPerColumn);
      if (off + 8 > cols.length) return true;
      for (let c = 0; c < 8; c++) {
        const m = cols[off + c];
        for (let y = 0; y < 5; y++) if ((m >> y) & 1) out[(y + 1) * 8 + c] = 3;
      }
      return false;
    },
  };
};

const effects = new Map<string, EffectFactory>([
  ["fireworks", fireworks],
  ["target", target],
  ["stars", stars],
  ["spinner", () => spinner()],
]);

/** Add or replace a named effect. */
export function registerEffect(name: string, factory: EffectFactory): void {
  effects.set(name, factory);
}

export function getEffect(name: string): EffectFactory | undefined {
  return effects.get(name);
}

export function listEffects(): string[] {
  return [...effects.keys()];
}
