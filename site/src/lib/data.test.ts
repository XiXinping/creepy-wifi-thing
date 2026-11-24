import { describe, expect, it } from "vitest";
import { K, baseline, score, threshold } from "./data";

describe("detection score", () => {
  it("is the per-frame median, so one always-noisy subcarrier can't trigger it", () => {
    const v = new Uint8Array(2 * K).fill(10);
    v[5] = 255; // a hot subcarrier in frame 0
    v.fill(200, K, K + K / 2 + 1); // frame 1: most subcarriers high
    expect(score(v)).toEqual([10, 200]);
  });

  it("threshold sits just above every quiet frame", () => {
    expect(threshold([80, 100], [90])).toBeCloseTo(105);
  });

  it("baseline is a per-subcarrier ceiling", () => {
    const v = new Uint8Array(100 * K).map((_, i) => (i % K === 3 ? 200 : 20));
    const b = baseline(v);
    expect(b[3]).toBe(200);
    expect(b[0]).toBe(20);
  });
});
