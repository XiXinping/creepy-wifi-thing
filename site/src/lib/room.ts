// Room metres ↔ stage pixels. The last rise frame is a straight-down view of the floor plane,
// so the mapping is linear: the frame's extent in metres (rise.json) stretched over its draw rect.
import type { Vec } from "./physics";

export type Rise = {
  frames: number;
  attach?: number; // leading frames before the rise proper (board slides right, antenna plugs in)
  room: { w: number; h: number };
  tx: [number, number];
  rx: [number, number];
  frame: { x0: number; x1: number; y0: number; y1: number };
};
export type Rect = { x: number; y: number; w: number; h: number };

export const ASPECT = 16 / 10;

/** Cover-fit an image of `aspect` into cw×ch, but never wider than `maxW`×cw (keeps the subject on portrait). */
export function cover(aspect: number, cw: number, ch: number, maxW = Infinity): Rect {
  const w = Math.min(Math.max(cw, ch * aspect), cw * maxW);
  const h = w / aspect;
  return { x: (cw - w) / 2, y: (ch - h) / 2, w, h };
}

export const heroFit = (cw: number, ch: number) => cover(ASPECT, cw, ch, 1.6);

/** Cover-fit the plan frame, shrunk if needed so the whole room (plus a margin) stays on screen.
 *  When that letterboxes it (portrait), its top edge sits at `top` so text fits below. */
export function roomFit(r: Rise, cw: number, ch: number, top = ch * 0.06, margin = 0.15): Rect {
  const fw = r.frame.x1 - r.frame.x0;
  const fh = r.frame.y1 - r.frame.y0;
  const s = Math.min(Math.max(cw / fw, ch / fh), cw / (r.room.w + 2 * margin), ch / (r.room.h + 2 * margin));
  const w = fw * s;
  const h = fh * s;
  return { x: (cw - w) / 2, y: Math.min((ch - h) / 2, top), w, h };
}

export const lerpRect = (a: Rect, b: Rect, t: number): Rect => ({
  x: a.x + (b.x - a.x) * t,
  y: a.y + (b.y - a.y) * t,
  w: a.w + (b.w - a.w) * t,
  h: a.h + (b.h - a.h) * t,
});

/** Room metres → CSS px within the stage, plus pixels per metre. */
export function toScreen(r: Rise, rect: Rect) {
  const s = rect.w / (r.frame.x1 - r.frame.x0);
  const at = (p: Vec) => ({ x: rect.x + (p.x - r.frame.x0) * s, y: rect.y + (r.frame.y1 - p.y) * s });
  return { at, s };
}

/** Affine [ax, bx, ay, by] taking gl_FragCoord (device px, origin bottom-left) to room metres. */
export function fromDevice(r: Rise, rect: Rect, dpr: number, heightPx: number): [number, number, number, number] {
  const s = rect.w / (r.frame.x1 - r.frame.x0);
  return [1 / (s * dpr), r.frame.x0 - rect.x / s, 1 / (s * dpr), r.frame.y1 - (heightPx / dpr - rect.y) / s];
}
