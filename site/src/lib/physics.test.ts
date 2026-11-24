import { describe, expect, it } from "vitest";
import { C, DF, LAMBDA, clearance, csiAmplitude, dist, fresnelRadius, fresnelZone, imageSources, specular } from "./physics";

const tx = { x: 1, y: 1.5 };
const rx = { x: 4, y: 2 };

describe("physics", () => {
  it("each wall bounce flips the wave", () => {
    const gains = (o: number) => imageSources(tx, 6, 4, 2).filter((p) => p.order === o).map((p) => p.gain);
    expect(gains(0)).toEqual([1]);
    for (const g of gains(1)) expect(g).toBeCloseTo(-0.6, 12);
    for (const g of gains(2)) expect(g).toBeCloseTo(0.36, 12);
  });

  it("a ball on the line blocks it, one off to the side doesn't", () => {
    const [a, b] = [{ x: 0, y: 0 }, { x: 4, y: 0 }];
    expect(clearance(a, b, { x: 2, y: 0.05 }, 0.2)).toBe(0);
    expect(clearance(a, b, { x: 2, y: 0.5 }, 0.2)).toBe(1);
    expect(clearance(a, b, { x: 5, y: 0 }, 0.2)).toBe(1); // beyond the end: not between them
    expect(clearance(a, b, { x: 2, y: 0.2 }, 0.2)).toBeCloseTo(0.5, 12); // grazing
  });

  it("a ball reflects at its surface, angle in = angle out", () => {
    // symmetric: straight below the centre
    const p = specular({ x: -1, y: 0 }, { x: 1, y: 0 }, { x: 0, y: 1 }, 0.2);
    expect(p.x).toBeCloseTo(0, 6);
    expect(p.y).toBeCloseTo(0.8, 6);
    // asymmetric: the surface normal bisects the directions to tx and rx
    const [t, r, c] = [{ x: -1, y: 0 }, { x: 3, y: 0.5 }, { x: 0.5, y: 1.2 }];
    const q = specular(t, r, c, 0.2, 7200);
    const u = (a: { x: number; y: number }) => ({ x: (a.x - q.x) / dist(a, q), y: (a.y - q.y) / dist(a, q) });
    const n = u(c);
    const [a, b] = [u(t), u(r)];
    expect(-(a.x * n.x + a.y * n.y)).toBeCloseTo(-(b.x * n.x + b.y * n.y), 2);
  });

  it("line of sight alone is flat across subcarriers", () => {
    const los = imageSources(tx, 6, 4, 0);
    expect(los).toHaveLength(1);
    const a = csiAmplitude(los, rx, -28);
    expect(csiAmplitude(los, rx, 28)).toBeCloseTo(a, 12);
  });

  it("first-order images are the four wall mirrors", () => {
    const first = imageSources(tx, 6, 4, 1).filter((p) => p.order === 1).map((p) => p.src);
    expect(first).toEqual(expect.arrayContaining([
      { x: -1, y: 1.5 }, { x: 11, y: 1.5 }, { x: 1, y: -1.5 }, { x: 1, y: 6.5 },
    ]));
  });

  it("two paths fade with a period of c/Δd in frequency", () => {
    const a = { src: { x: 0, y: 0 }, order: 0, gain: 1 };
    const b = { src: { x: 0, y: 0 }, order: 1, gain: 1 };
    const r = { x: 3, y: 0 };
    // second path longer by c/(32·Δf) ≈ 30 m -> amplitude repeats every 32 subcarriers
    const shifted = { ...b, src: { x: -C / (32 * DF), y: 0 } };
    const period = Math.round(C / (dist(shifted.src, r) - dist(a.src, r)) / DF);
    expect(period).toBe(32);
    const amp = (k: number) => csiAmplitude([a, shifted], r, k);
    expect(amp(-16)).toBeCloseTo(amp(16), 9);
  });

  it("first Fresnel radius at the midpoint is sqrt(λd)/2", () => {
    const d = 3;
    expect(fresnelRadius(1, d / 2, d / 2)).toBeCloseTo(Math.sqrt(LAMBDA * d) / 2, 12);
  });

  it("points on the line of sight are in zone 1, the r1 boundary starts zone 2", () => {
    const a = { x: 0, y: 0 };
    const b = { x: 3, y: 0 };
    expect(fresnelZone({ x: 1.5, y: 0 }, a, b)).toBe(1);
    expect(fresnelZone({ x: 1.5, y: fresnelRadius(1, 1.5, 1.5) * 1.01 }, a, b)).toBe(2);
  });
});
