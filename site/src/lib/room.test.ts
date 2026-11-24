import { describe, expect, it } from "vitest";
import { cover, fromDevice, roomFit, toScreen, type Rise } from "./room";

const rise: Rise = {
  frames: 96,
  room: { w: 6, h: 3.75 },
  tx: [1.375, 1.625],
  rx: [4.625, 2.125],
  frame: { x0: -0.6, x1: 6.6, y0: -0.375, y1: 4.125 },
};

describe("room mapping", () => {
  it("puts the boards where the 640×400 plan-view preview shows them", () => {
    const { at } = toScreen(rise, cover(1.6, 640, 400));
    const tx = at({ x: rise.tx[0], y: rise.tx[1] });
    const rx = at({ x: rise.rx[0], y: rise.rx[1] });
    expect(tx.x).toBeCloseTo(175.6, 0);
    expect(tx.y).toBeCloseTo(222.2, 0);
    expect(rx.x).toBeCloseTo(464.4, 0);
    expect(rx.y).toBeCloseTo(177.8, 0);
  });

  it("keeps the whole room on a portrait phone", () => {
    const { at } = toScreen(rise, roomFit(rise, 390, 844));
    for (const p of [{ x: 0, y: 0 }, { x: 6, y: 3.75 }]) {
      const q = at(p);
      expect(q.x).toBeGreaterThanOrEqual(0);
      expect(q.x).toBeLessThanOrEqual(390);
      expect(q.y).toBeGreaterThanOrEqual(0);
      expect(q.y).toBeLessThanOrEqual(844);
    }
  });

  it("is plain cover-fit on a 16:10 desktop", () => {
    const a = roomFit(rise, 1440, 900);
    const b = cover(1.6, 1440, 900);
    for (const k of ["x", "y", "w", "h"] as const) expect(a[k]).toBeCloseTo(b[k], 6);
  });

  it("fragment coords invert the screen mapping", () => {
    const rect = roomFit(rise, 1280, 720);
    const dpr = 1.5;
    const H = 720 * dpr;
    const [ax, bx, ay, by] = fromDevice(rise, rect, dpr, H);
    const q = toScreen(rise, rect).at({ x: 2, y: 3 });
    expect(ax * q.x * dpr + bx).toBeCloseTo(2, 9);
    expect(ay * (H - q.y * dpr) + by).toBeCloseTo(3, 9);
  });
});
