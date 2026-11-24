import { describe, expect, it } from "vitest";
import { C, DF, LAMBDA, csiAmplitude, dist, fresnelRadius, fresnelZone, imageSources } from "./physics";

const tx = { x: 1, y: 1.5 };
const rx = { x: 4, y: 2 };

describe("physics", () => {
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
