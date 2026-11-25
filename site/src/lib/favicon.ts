// The tab icon: a travelling wave, redrawn ~12×/s on the page's rAF loop.
import { css, token } from "./color";
import { onFrame, reduced } from "./scroll";

const [deep, bloom] = ["deep", "bloom"].map(token);
const S = 64;
const c = Object.assign(document.createElement("canvas"), { width: S, height: S });
const ctx = c.getContext("2d")!;
const link = document.querySelector<HTMLLinkElement>('link[rel="icon"]')!;
link.type = "image/png";

function draw(t: number) {
  ctx.clearRect(0, 0, S, S);
  ctx.fillStyle = css(deep);
  ctx.beginPath();
  ctx.roundRect(0, 0, S, S, 14);
  ctx.fill();
  ctx.strokeStyle = css(bloom);
  ctx.lineWidth = 4.5;
  ctx.lineCap = ctx.lineJoin = "round";
  ctx.beginPath();
  for (let x = 6; x <= 58; x += 2) {
    const env = Math.sin(((x - 6) / 52) * Math.PI);
    const y = 32 + env * 17 * Math.sin(x * 0.28 - t * 5);
    x === 6 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.stroke();
  link.href = c.toDataURL();
}

draw(0);
if (!reduced) {
  let shown = 0;
  onFrame((now) => {
    if (document.hidden || now - shown < 1 / 12) return;
    shown = now;
    draw(now);
  });
}
