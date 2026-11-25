import { getFace } from "./frames";
import { DEFAULT_PALETTE, GEOMETRY, boxHeight, boxWidth, levelColour, type Palette } from "./theme";

export interface SvgOptions {
  /** Width and height of the square output, px. Default 1024. */
  size?: number;
  palette?: Partial<Palette>;
  /** Draw the shadow tile. Default true. */
  shadow?: boolean;
  /** Fill behind everything, or null for transparent. Default null. */
  background?: string | null;
  /** Empty space round the artwork, as a fraction of size. Default 0.08. */
  margin?: number;
}

const f = (n: number) => +n.toFixed(2);

/** Render one frame (64 levels) as an SVG string, matching the on screen avatar. */
export function frameToSvg(levels: ArrayLike<number>, opts: SvgOptions = {}): string {
  const { size = 1024, shadow = true, background = null, margin = 0.08 } = opts;
  const pal: Palette = { ...DEFAULT_PALETTE, ...opts.palette };
  const g = GEOMETRY;

  const w = boxWidth(shadow), h = boxHeight(shadow);
  const P = (size * (1 - 2 * margin)) / Math.max(w, h);
  const ox = (size - w * P) / 2, oy = (size - h * P) / 2;

  const skewAbout = (x: number, y: number) => {
    const cx = f(x + P / 2), cy = f(y + P / 2);
    return `translate(${cx} ${cy}) skewY(${g.skewY}) skewX(${g.skewX}) translate(${-cx} ${-cy})`;
  };
  const rx = f(P * g.radius);

  const pad = P * g.padding, gap = P * g.gap;
  const cell = (P - 2 * pad - 7 * gap) / 8;
  const crx = f(cell * g.pixelRadius);
  const pixels: string[] = [];
  for (let r = 0; r < 8; r++)
    for (let c = 0; c < 8; c++)
      pixels.push(
        `<rect x="${f(ox + pad + c * (cell + gap))}" y="${f(oy + pad + r * (cell + gap))}" width="${f(cell)}" height="${f(cell)}" rx="${crx}" fill="${levelColour(pal, levels[r * 8 + c])}"/>`,
      );

  const sx = ox + P * g.shadowX, sy = oy + P * g.shadowY;
  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">`,
    background ? `<rect width="${size}" height="${size}" fill="${background}"/>` : "",
    shadow ? `<rect x="${f(sx)}" y="${f(sy)}" width="${f(P)}" height="${f(P)}" rx="${rx}" fill="${pal.shadow}" transform="${skewAbout(sx, sy)}"/>` : "",
    `<g transform="${skewAbout(ox, oy)}">`,
    `<rect x="${f(ox)}" y="${f(oy)}" width="${f(P)}" height="${f(P)}" rx="${rx}" fill="${pal.panel}"/>`,
    ...pixels,
    `</g>`,
    `</svg>`,
  ].join("\n");
}

export function faceToSvg(name: string, opts: SvgOptions = {}): string {
  return frameToSvg(getFace(name).levels, opts);
}
