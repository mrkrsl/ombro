import type { EffectRun } from "./effects";

/**
 * Pixel engine. Each of the 64 pixels eases toward a target brightness.
 * It only runs requestAnimationFrame while something is changing, so a
 * settled avatar costs nothing.
 */
export class PixelEngine {
  private cur = new Float32Array(64);
  private tgt = new Float32Array(64);
  private pend = new Float32Array(64);
  private at = new Float64Array(64);
  private last: string[] = new Array(64).fill("");
  private raf = 0;
  private lt = 0;
  private fx: { run: EffectRun; t0: number; lastStep: number; done: (finished: boolean) => void } | null = null;
  /** easing time constant in ms */
  tau = 60;

  constructor(private ramp: string[], private paint: (i: number, colour: string) => void) {
    for (let i = 0; i < 64; i++) this.draw(i);
  }

  setRamp(ramp: string[]) {
    this.ramp = ramp;
    this.last.fill("");
    for (let i = 0; i < 64; i++) this.draw(i);
  }

  /** Ease toward a frame. `stagger` spreads the start of each pixel over that many ms. */
  show(levels: ArrayLike<number>, stagger = 0) {
    const n = performance.now();
    for (let i = 0; i < 64; i++) {
      this.pend[i] = levels[i];
      this.at[i] = n + (stagger ? Math.random() * stagger : 0);
    }
    this.kick();
  }

  /** Jump straight to a frame with no easing. */
  snap(levels: ArrayLike<number>) {
    this.cancelEffect();
    for (let i = 0; i < 64; i++) {
      this.cur[i] = this.tgt[i] = this.pend[i] = levels[i];
      this.at[i] = 0;
      this.draw(i);
    }
  }

  /** The frame currently being shown or eased toward, as whole levels. */
  current(): number[] {
    return Array.from(this.pend, (v) => Math.max(0, Math.min(3, Math.round(v))));
  }

  /** Play an effect. Resolves true when it finishes, false if it was cancelled. */
  runEffect(run: EffectRun): Promise<boolean> {
    this.cancelEffect();
    return new Promise((done) => {
      if (run.tau) this.tau = run.tau;
      this.fx = { run, t0: performance.now(), lastStep: -1e9, done };
      this.kick();
    });
  }

  cancelEffect() {
    const fx = this.fx;
    this.fx = null;
    fx?.done(false);
  }

  destroy() {
    this.cancelEffect();
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
  }

  private kick() {
    if (!this.raf) {
      this.lt = performance.now();
      this.raf = requestAnimationFrame(this.loop);
    }
  }

  private draw(i: number) {
    const q = Math.round(Math.max(0, Math.min(3, this.cur[i])) * 32);
    const c = this.ramp[q];
    if (c !== this.last[i]) {
      this.last[i] = c;
      this.paint(i, c);
    }
  }

  private loop = (n: number) => {
    const dt = Math.min(64, n - this.lt);
    this.lt = n;

    const fx = this.fx;
    if (fx) {
      const t = n - fx.t0;
      let finished = t > fx.run.maxMs;
      if (!finished && n - fx.lastStep >= fx.run.step) {
        fx.lastStep = n;
        const out = new Float32Array(64);
        finished = fx.run.frame(out, t);
        this.show(out, 0);
      }
      if (finished) {
        this.fx = null;
        fx.done(true);
      }
    }

    const k = 1 - Math.exp(-dt / this.tau);
    let busy = !!this.fx;
    for (let i = 0; i < 64; i++) {
      if (n >= this.at[i]) this.tgt[i] = this.pend[i];
      else busy = true;
      const d = this.tgt[i] - this.cur[i];
      if (Math.abs(d) < 0.01) this.cur[i] = this.tgt[i];
      else { this.cur[i] += d * k; busy = true; }
      this.draw(i);
    }
    this.raf = busy ? requestAnimationFrame(this.loop) : 0;
  };
}
