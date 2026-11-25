export interface Palette {
  /** pixel levels 0 to 3 */
  off: string;
  dim: string;
  mid: string;
  on: string;
  /** the dark tile behind the pixels */
  panel: string;
  /** the offset shadow tile */
  shadow: string;
}

export const DEFAULT_PALETTE: Palette = {
  off: "#1e3441",
  dim: "#36596a",
  mid: "#6f9cad",
  on: "#ffffff",
  panel: "#0a1418",
  shadow: "#97bfcc",
};

/**
 * Shared geometry, as fractions of the panel size. Used by the React
 * component and by the SVG exporter so both look identical.
 */
export const GEOMETRY = {
  skewY: -1.8, // degrees
  skewX: 0.9, // degrees
  radius: 0.085, // panel corner radius
  padding: 0.075, // inset from panel edge to the pixels
  gap: 0.018, // gap between pixels
  pixelRadius: 0.28, // pixel corner radius, as a fraction of the pixel
  shadowX: 0.15, // shadow offset right
  shadowY: 0.13, // shadow offset down
};

/** Outer box of panel plus shadow, as a multiple of the panel size. */
export const boxWidth = (shadow: boolean) => (shadow ? 1 + GEOMETRY.shadowX + 0.02 : 1.02);
export const boxHeight = (shadow: boolean) => (shadow ? 1 + GEOMETRY.shadowY + 0.02 : 1.02);

function hexToRgb(h: string): [number, number, number] {
  const s = h.replace("#", "");
  const f = s.length === 3 ? s.split("").map((c) => c + c).join("") : s;
  return [0, 2, 4].map((i) => parseInt(f.slice(i, i + 2), 16)) as [number, number, number];
}

/** 97 colours from off to on, 32 steps per level, for smooth fades. */
export function buildRamp(p: Palette): string[] {
  const stops = [p.off, p.dim, p.mid, p.on].map(hexToRgb);
  const ramp: string[] = [];
  for (let q = 0; q <= 96; q++) {
    const v = q / 32, a = Math.min(2, Math.floor(v)), t = v - a;
    const c = stops[a].map((x, j) => Math.round(x + (stops[a + 1][j] - x) * t));
    ramp.push(`rgb(${c.join(",")})`);
  }
  return ramp;
}

export function levelColour(p: Palette, level: number): string {
  return [p.off, p.dim, p.mid, p.on][Math.max(0, Math.min(3, Math.round(level)))];
}
