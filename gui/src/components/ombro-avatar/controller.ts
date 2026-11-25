import { PixelEngine } from "./engine";
import { blinkFrame, facesByMood, getFace, glanceFrame, winkFrame } from "./frames";
import { getEffect, scrollText, spinner, type EffectRun } from "./effects";

const rand = (a: number, b: number) => a + Math.random() * (b - a);
const pick = <T,>(a: readonly T[]): T => a[Math.floor(Math.random() * a.length)];

/** Order of the thinking cycle. */
const SEQUENCE = ["face", "spinner", "face", "text", "face", "effect", "face", "text"] as const;

export interface IdleOptions {
  /** shortest and longest pause between idle gestures, ms */
  minMs?: number;
  maxMs?: number;
  /** chance that a gesture is a wink rather than a blink */
  winkChance?: number;
  /** chance that a gesture is a glance */
  glanceChance?: number;
}

export interface ThinkOptions {
  /** faces to cycle through. Defaults to the thinking and neutral moods. */
  faces?: string[];
  /** words to scroll now and then, taken in turn */
  texts?: string[];
  /** effects played now and then between faces. Default stars, target and fireworks. */
  effects?: string[];
  /** ms each face is held */
  holdMs?: [number, number];
}

/**
 * Framework free controller. Wraps the pixel engine with the behaviours
 * the app needs: faces, effects, idle gestures and the thinking cycle.
 * Every new behaviour cancels the previous one.
 */
export class AvatarController {
  private gen = 0;
  private base: readonly number[];
  private idleTimer: ReturnType<typeof setTimeout> | undefined;
  private idle: Required<IdleOptions> | null = null;
  private visible = true;
  face: string;

  constructor(private engine: PixelEngine, private reducedMotion = false) {
    this.face = "calm";
    this.base = getFace("calm").levels;
  }

  /** Cancel whatever is running. */
  stop() {
    this.gen++;
    this.engine.cancelEffect();
    this.stopIdle();
  }

  destroy() {
    this.stop();
    this.engine.destroy();
  }

  setVisible(v: boolean) {
    this.visible = v;
  }

  setFace(name: string, stagger = 160) {
    this.face = name;
    this.base = getFace(name).levels;
    this.engine.tau = 70;
    if (this.reducedMotion) this.engine.snap(this.base);
    else this.engine.show(this.base, stagger);
  }

  /** Show a face immediately, with no animation. */
  snapFace(name: string) {
    this.stop();
    this.face = name;
    this.base = getFace(name).levels;
    this.engine.snap(this.base);
  }

  /** Play a named effect or an effect run. Resolves true if it played to the end. */
  play(effect: string | EffectRun): Promise<boolean> {
    const run = typeof effect === "string" ? getEffect(effect)?.() : effect;
    if (!run || this.reducedMotion) return Promise.resolve(false);
    return this.engine.runEffect(run);
  }

  scroll(text: string): Promise<boolean> {
    return this.play(scrollText(text));
  }

  async blink() {
    await this.flash(blinkFrame(this.base), 120);
  }

  async wink() {
    await this.flash(winkFrame(this.base), 380);
  }

  async glance(dx = pick([-1, 1]), dy = pick([-1, 0, 0, 1]), ms = rand(600, 1300)) {
    await this.flash(glanceFrame(this.base, dx, dy), ms);
  }

  private async flash(frame: number[], ms: number) {
    if (this.reducedMotion) return;
    const g = this.gen;
    this.engine.tau = 45;
    this.engine.show(frame);
    await new Promise((r) => setTimeout(r, ms));
    if (g === this.gen) this.engine.show(this.base);
  }

  /** Message rule: optional effect, then resolve into a face. */
  async introduce(face: string, effect?: string | null): Promise<boolean> {
    this.stop();
    const g = this.gen;
    if (effect && !this.reducedMotion) {
      const finished = await this.play(effect);
      if (!finished || g !== this.gen) return false;
      this.setFace(face, 450);
    } else {
      this.setFace(face, this.reducedMotion ? 0 : 200);
    }
    return true;
  }

  /** Occasional blinks, winks and glances. Never continuous. */
  startIdle(opts: IdleOptions = {}) {
    if (this.reducedMotion) return;
    this.idle = { minMs: 5000, maxMs: 12000, winkChance: 0.12, glanceChance: 0.25, ...opts };
    this.scheduleIdle();
  }

  stopIdle() {
    this.idle = null;
    clearTimeout(this.idleTimer);
  }

  private scheduleIdle() {
    const o = this.idle;
    if (!o) return;
    clearTimeout(this.idleTimer);
    this.idleTimer = setTimeout(async () => {
      if (!this.idle) return;
      if (this.visible) {
        const r = Math.random();
        if (r < o.winkChance) await this.wink();
        else if (r < o.winkChance + o.glanceChance) await this.glance();
        else {
          await this.blink();
          if (Math.random() < 0.2) { await new Promise((res) => setTimeout(res, 160)); await this.blink(); }
        }
      }
      this.scheduleIdle();
    }, rand(o.minMs, o.maxMs));
  }

  /** Thinking rule: cycle faces, the spinner and scrolling words until stopped. */
  async think(opts: ThinkOptions = {}) {
    this.stop();
    const g = this.gen;
    const faces = opts.faces?.length ? opts.faces : facesByMood("thinking", "neutral");
    const texts = opts.texts ?? ["THINKING", "WORKING"];
    const fx = opts.effects ?? ["stars", "target", "fireworks"];
    const [hMin, hMax] = opts.holdMs ?? [1600, 2800];
    const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

    if (this.reducedMotion) { this.setFace(faces[0]); return; }

    let step = 0;
    let said = 0;
    let face = this.face;
    while (g === this.gen) {
      const kind = SEQUENCE[step % SEQUENCE.length];
      if (kind === "spinner") {
        await this.play(spinner(rand(1600, 2400)));
      } else if (kind === "text" && texts.length) {
        await this.scroll(texts[said++ % texts.length]);
      } else if (kind === "effect" && fx.length) {
        await this.play(pick(fx));
      } else {
        const options = faces.filter((f) => f !== face);
        face = pick(options.length ? options : faces);
        this.setFace(face, 220);
        await wait(rand(hMin, hMax) / 2);
        if (g !== this.gen) return;
        if (Math.random() < 0.6) await this.glance();
        await wait(rand(hMin, hMax) / 2);
      }
      step++;
    }
  }
}
