import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, type CSSProperties } from "react";
import { AvatarController, type IdleOptions, type ThinkOptions } from "./controller";
import { PixelEngine } from "./engine";
import { facesByMood, hasFace, type Mood } from "./frames";
import { DEFAULT_PALETTE, GEOMETRY, boxHeight, boxWidth, buildRamp, type Palette } from "./theme";

export type AvatarMode = "message" | "thinking" | "static" | "cycle";

export interface OmbroAvatarProps {
  /** Panel size in px. Default 32. */
  size?: number;
  /**
   * message: optional intro effect, resolves to a face, then only occasional blinks and winks.
   * thinking: cycles thinking faces, the spinner and scrolling words until the mode changes.
   * static: shows the face with no animation at all.
   * cycle: no face; the other effects one after another, for work going on (a job).
   */
  mode?: AvatarMode;
  /** A fixed face. If left out, one is picked from `mood` using `seed`. */
  face?: string;
  /** Moods to pick a face from when `face` is not given. Default "positive". */
  mood?: Mood | Mood[];
  /** Same seed, same face and intro. Pass the message id so a message keeps its face. */
  seed?: string | number;
  /** Intro effect, or a list to pick from with the seed. null for none. Default fireworks or target. */
  intro?: string | string[] | null;
  /** Play the intro. Set false for messages loaded from history so a long thread does not all fire at once. */
  animateIn?: boolean;
  /** Occasional idle gestures in message mode. true, false or options. Default true. */
  idle?: boolean | IdleOptions;
  /** Options for thinking mode. */
  think?: ThinkOptions;
  palette?: Partial<Palette>;
  /** Show the offset shadow tile. Default true. */
  shadow?: boolean;
  /** Slant the panel. Default true; false draws it square, which reads better very small. */
  skew?: boolean;
  /** Accessible label. Default "Ombro lead". */
  label?: string;
  className?: string;
  style?: CSSProperties;
}

export interface OmbroAvatarHandle {
  controller: AvatarController | null;
  setFace(name: string): void;
  play(effect: string): Promise<boolean>;
  scroll(text: string): Promise<boolean>;
  blink(): Promise<void>;
  wink(): Promise<void>;
}

/** Small deterministic random from a seed, so a message always gets the same face. */
function seeded(seed: string | number | undefined): () => number {
  if (seed === undefined) return Math.random;
  let h = 2166136261;
  for (const ch of String(seed)) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  let a = h >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const prefersReducedMotion = () =>
  typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export const OmbroAvatar = forwardRef<OmbroAvatarHandle, OmbroAvatarProps>(function OmbroAvatar(
  {
    size = 32,
    mode = "message",
    face,
    mood = "positive",
    seed,
    intro = ["fireworks", "target"],
    animateIn = true,
    idle = true,
    think,
    palette,
    shadow = true,
    skew: slant = true,
    label = "Ombro lead",
    className,
    style,
  },
  ref,
) {
  const rootRef = useRef<HTMLSpanElement>(null);
  const cells = useRef<(HTMLSpanElement | null)[]>([]);
  const engineRef = useRef<PixelEngine | null>(null);
  const ctrlRef = useRef<AvatarController | null>(null);

  const paletteKey = JSON.stringify(palette ?? {});
  const pal = useMemo<Palette>(() => ({ ...DEFAULT_PALETTE, ...palette }), [paletteKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Create the engine once.
  useEffect(() => {
    const engine = new PixelEngine(buildRamp(pal), (i, c) => {
      const el = cells.current[i];
      if (el) el.style.backgroundColor = c;
    });
    const ctrl = new AvatarController(engine, prefersReducedMotion());
    engineRef.current = engine;
    ctrlRef.current = ctrl;

    let io: IntersectionObserver | undefined;
    if (rootRef.current && typeof IntersectionObserver !== "undefined") {
      io = new IntersectionObserver(([e]) => ctrl.setVisible(e.isIntersecting));
      io.observe(rootRef.current);
    }
    return () => {
      io?.disconnect();
      ctrl.destroy();
      engineRef.current = null;
      ctrlRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    engineRef.current?.setRamp(buildRamp(pal));
  }, [pal]);

  // Behaviour. Keys keep this from re-running when the parent re-renders with equal props.
  const moodKey = Array.isArray(mood) ? mood.join(",") : mood;
  const introKey = intro === null ? "" : Array.isArray(intro) ? intro.join(",") : intro;
  const idleKey = JSON.stringify(idle);
  const thinkKey = JSON.stringify(think ?? {});

  useEffect(() => {
    const c = ctrlRef.current;
    if (!c) return;
    let cancelled = false;

    if (mode === "thinking") {
      void c.think(think);
      return () => c.stop();
    }
    if (mode === "cycle") {
      void c.cycle();
      return () => c.stop();
    }

    const rng = seeded(seed);
    const moods = Array.isArray(mood) ? mood : [mood];
    const pool = facesByMood(...moods);
    const faceName = face && hasFace(face) ? face : pool.length ? pool[Math.floor(rng() * pool.length)] : "calm";

    if (mode === "static") {
      c.snapFace(faceName);
      return () => c.stop();
    }

    const fxList = intro === null ? [] : Array.isArray(intro) ? intro : [intro];
    const fx = animateIn && fxList.length ? fxList[Math.floor(rng() * fxList.length)] : null;

    const start = animateIn ? c.introduce(faceName, fx) : (c.snapFace(faceName), Promise.resolve(true));
    void start.then((ok) => {
      if (ok && !cancelled && idle) c.startIdle(idle === true ? {} : idle);
    });
    return () => {
      cancelled = true;
      c.stop();
    };
  }, [mode, face, moodKey, seed, introKey, animateIn, idleKey, thinkKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useImperativeHandle(
    ref,
    () => ({
      get controller() { return ctrlRef.current; },
      setFace: (n) => ctrlRef.current?.setFace(n),
      play: (e) => ctrlRef.current?.play(e) ?? Promise.resolve(false),
      scroll: (t) => ctrlRef.current?.scroll(t) ?? Promise.resolve(false),
      blink: () => ctrlRef.current?.blink() ?? Promise.resolve(),
      wink: () => ctrlRef.current?.wink() ?? Promise.resolve(),
    }),
    [],
  );

  const g = GEOMETRY;
  const skew = slant ? `skewY(${g.skewY}deg) skewX(${g.skewX}deg)` : "none";
  const tile: CSSProperties = {
    position: "absolute",
    width: size,
    height: size,
    borderRadius: size * g.radius,
    transform: skew,
  };

  return (
    <span
      ref={rootRef}
      role="img"
      aria-label={label}
      className={className}
      style={{
        position: "relative",
        display: "inline-block",
        verticalAlign: "top",
        flex: "none",
        width: size * boxWidth(shadow),
        height: size * boxHeight(shadow),
        ...style,
      }}
    >
      {shadow && <span style={{ ...tile, left: size * g.shadowX, top: size * g.shadowY, background: pal.shadow }} />}
      <span
        style={{
          ...tile,
          left: 0,
          top: 0,
          background: pal.panel,
          padding: size * g.padding,
          boxSizing: "border-box",
          display: "grid",
          gridTemplateColumns: "repeat(8, 1fr)",
          gridTemplateRows: "repeat(8, 1fr)",
          gap: size * g.gap,
        }}
      >
        {Array.from({ length: 64 }, (_, i) => (
          <span
            key={i}
            ref={(el) => { cells.current[i] = el; }}
            style={{ display: "block", borderRadius: `${g.pixelRadius * 100}%`, background: pal.off }}
          />
        ))}
      </span>
    </span>
  );
});
