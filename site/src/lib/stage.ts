import type { Rect } from "./room";
import { css, token } from "./color";

/** Match a canvas's backing store to its CSS box; returns its CSS size and the pixel ratio used. */
export function sizeCanvas(c: HTMLCanvasElement, maxDpr = 2) {
  const dpr = Math.min(devicePixelRatio || 1, maxDpr);
  const w = c.clientWidth;
  const h = c.clientHeight;
  const bw = Math.round(w * dpr);
  const bh = Math.round(h * dpr);
  const changed = c.width !== bw || c.height !== bh;
  if (changed) {
    c.width = bw;
    c.height = bh;
  }
  return { w, h, dpr, changed };
}

export const portrait = (w: number, h: number) => h > w;

/** Where a data panel sits on a quiet stage: right of the text column, or above the bottom text on portrait. */
export const panelRect = (w: number, h: number): Rect =>
  portrait(w, h) ? { x: 16, y: h * 0.07, w: w - 32, h: h * 0.42 } : { x: w * 0.45, y: h * 0.14, w: w * 0.5, h: h * 0.72 };

let font = "";
let halo = "";
/** A terse chart label in the body face, haloed in the page colour so it reads over the field. */
export function label(ctx: CanvasRenderingContext2D, s: string, x: number, y: number, fill: string, align: CanvasTextAlign = "left", vertical = false) {
  font ||= `500 13px ${getComputedStyle(document.documentElement).getPropertyValue("--body")}`;
  halo ||= css(token("deep"));
  ctx.save();
  ctx.font = font;
  ctx.fillStyle = fill;
  ctx.textAlign = align;
  ctx.textBaseline = "middle";
  ctx.translate(x, y);
  if (vertical) ctx.rotate(-Math.PI / 2);
  ctx.strokeStyle = halo;
  ctx.lineWidth = 4;
  ctx.lineJoin = "round";
  ctx.globalAlpha = parseFloat(fill.slice(fill.lastIndexOf(",") + 1)); // halo fades with the label (fill is css() rgba)
  ctx.strokeText(s, 0, 0);
  ctx.globalAlpha = 1;
  ctx.fillText(s, 0, 0);
  ctx.restore();
}
