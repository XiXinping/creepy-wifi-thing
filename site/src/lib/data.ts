// The real recordings (made by tools/prep_csi.py): mean profiles + time-major u8 matrices.
export const K = 56;

export type Csi = {
  rateHz: number;
  subcarriers: number[];
  profiles: Record<string, number[]>;
  matrices: Record<string, { frames: number; sourceHz: number }>;
};
export type Rec = { frames: number; amp: Uint8Array; var: Uint8Array };

let cache: Promise<{ meta: Csi; recs: Record<string, Rec> }> | undefined;

export function loadCsi() {
  const base = `${import.meta.env.BASE_URL}data/`;
  const bytes = async (f: string) => new Uint8Array(await (await fetch(base + f)).arrayBuffer());
  return (cache ??= (async () => {
    const meta: Csi = await (await fetch(base + "csi.json")).json();
    const recs = Object.fromEntries(
      await Promise.all(
        Object.entries(meta.matrices).map(async ([name, m]) => {
          const [amp, v] = await Promise.all([bytes(`${name}.amp.u8`), bytes(`${name}.var.u8`)]);
          return [name, { frames: m.frames, amp, var: v }] as const;
        }),
      ),
    );
    return { meta, recs };
  })());
}

/** Detection score per frame: median over subcarriers (one DC-adjacent subcarrier is always noisy). */
export function score(v: Uint8Array): number[] {
  const out: number[] = [];
  for (let t = 0; t + K <= v.length; t += K) {
    const row = Array.from(v.subarray(t, t + K)).sort((a, b) => a - b);
    out.push((row[K / 2 - 1] + row[K / 2]) / 2);
  }
  return out;
}

/** Threshold: 5% above the highest score seen with nobody moving (empty and still recordings). */
export const threshold = (...quiet: number[][]) => 1.05 * Math.max(...quiet.flat());

/** Per-subcarrier variance ceiling of a quiet recording (99th percentile), the baseline motion is compared to. */
export function baseline(v: Uint8Array): number[] {
  const T = v.length / K;
  return Array.from({ length: K }, (_, k) => {
    const col = Array.from({ length: T }, (_, t) => v[t * K + k]).sort((a, b) => a - b);
    return Math.max(1, col[Math.floor(0.99 * (T - 1))]);
  });
}
