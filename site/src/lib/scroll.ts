// The page's single rAF loop: drives Lenis (unless reduced motion) and every scene's draw.
import Lenis from "lenis";

export const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

const lenis = reduced ? null : new Lenis();
const frames = new Set<(t: number) => void>();

export const onFrame = (fn: (t: number) => void) => frames.add(fn);

function loop(ms: number) {
  lenis?.raf(ms);
  for (const fn of frames) fn(ms / 1000);
  requestAnimationFrame(loop);
}
requestAnimationFrame(loop);

export const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

/** 0..1 between t=a and t=b. */
export const seg = (t: number, a: number, b: number) => clamp01((t - a) / (b - a));

/** Scroll progress through a pinned track (0 when its stage pins, 1 when it unpins). */
export function progress(track: HTMLElement) {
  const r = track.getBoundingClientRect();
  return {
    p: clamp01(-r.top / (r.height - innerHeight)),
    visible: r.bottom > 0 && r.top < innerHeight,
    pinned: r.top <= 0,
  };
}
