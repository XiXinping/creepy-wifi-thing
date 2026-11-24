// Scroll-scrubbed render sequence: loads WebP frames progressively and draws the nearest loaded one.
import { css, type RGB } from "./color";
import type { Rect } from "./room";

/** Frame indices that exist on disk per size, listed at build time (see renders.ts). */
export type Frames = { count: number; lg: number[]; sm: number[] };

const url = (shot: string, size: string, i: number) =>
  `${import.meta.env.BASE_URL}renders/${shot}/${size}/${String(i).padStart(4, "0")}.webp`;

export function sequence(shot: string, frames: Frames | null) {
  const imgs: HTMLImageElement[] = [];
  let started = false;
  let last = "";

  const size = innerWidth < 1000 ? (frames?.sm.length ? "sm" : "lg") : frames?.lg.length ? "lg" : "sm";
  const list = frames?.[size] ?? [];

  /** Start fetching: first and last frame, then every 8th, 4th, 2nd, rest — so scrubbing always has a near neighbour. */
  function load() {
    if (started || !frames) return;
    started = true;
    const end = frames.count - 1;
    const rank = (i: number) => (i === 0 || i === end ? 0 : i % 8 === 0 ? 1 : i % 4 === 0 ? 2 : i % 2 === 0 ? 3 : 4);
    const queue = [...list].sort((a, b) => rank(a) - rank(b) || a - b);
    const next = () => {
      const i = queue.shift();
      if (i === undefined) return;
      const img = new Image();
      img.decoding = "async";
      img.onload = () => {
        imgs[i] = img;
        next();
      };
      img.onerror = next;
      img.src = url(shot, size, i);
    };
    for (let n = 0; n < 4; n++) next();
  }

  /** Draw the frame nearest to progress t into rect (CSS px; ctx already scaled to CSS px).
   *  lift 0..1 raises anything darker than bg up to it (the render's unlit floor is black). */
  function draw(ctx: CanvasRenderingContext2D, t: number, rect: Rect, bg: RGB, lift = 0) {
    if (!frames) return;
    const want = Math.round(t * (frames.count - 1));
    let img: HTMLImageElement | undefined;
    for (let d = 0; d < frames.count && !img; d++) img = imgs[want - d] ?? imgs[want + d];
    const key = `${img?.src}|${rect.x}|${rect.y}|${rect.w}|${ctx.canvas.width}|${ctx.canvas.height}|${lift}`;
    if (key === last) return;
    last = key;
    const { width, height } = ctx.canvas;
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = css(bg);
    ctx.fillRect(0, 0, width, height);
    ctx.restore();
    if (!img) return;
    ctx.drawImage(img, rect.x, rect.y, rect.w, rect.h);
    if (lift > 0) {
      ctx.save();
      ctx.globalCompositeOperation = "lighten"; // per channel max: lit pixels keep their colour
      ctx.globalAlpha = lift;
      ctx.fillStyle = css(bg);
      ctx.fillRect(rect.x, rect.y, rect.w, rect.h);
      ctx.restore();
    }
    // letterboxed (portrait): feather the frame's top and bottom edges into the page
    if (rect.y > 0) {
      const f = rect.h * 0.15;
      for (const [y0, y1] of [[rect.y, rect.y + f], [rect.y + rect.h, rect.y + rect.h - f]]) {
        const g = ctx.createLinearGradient(0, y0, 0, y1);
        g.addColorStop(0, css(bg));
        g.addColorStop(1, css(bg, 0));
        ctx.fillStyle = g;
        ctx.fillRect(rect.x, Math.min(y0, y1), rect.w, f);
      }
    }
  }

  return { load, draw };
}
