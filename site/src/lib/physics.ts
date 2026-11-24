// Radio physics shared by the shader, the subcarrier strip and the tests.
// Numbers match the recordings: channel 7, legacy 20 MHz OFDM (see tools/prep_csi.py).

export const C = 299_792_458;
export const F0 = 2.442e9;
export const DF = 312.5e3;
export const LAMBDA = C / F0;
export const SUBCARRIERS = Array.from({ length: 57 }, (_, i) => i - 28).filter((k) => k !== 0);

export type Vec = { x: number; y: number };
export type Path = { src: Vec; order: number; gain: number };

/** Mirror images of `tx` in a W×H rectangular room, up to `maxOrder` wall bounces. */
export function imageSources(tx: Vec, w: number, h: number, maxOrder: number, reflectivity = 0.6): Path[] {
  const mirror = (i: number, p: number, L: number) => i * L + (i % 2 === 0 ? p : L - p);
  const out: Path[] = [];
  for (let i = -maxOrder; i <= maxOrder; i++)
    for (let j = -maxOrder; j <= maxOrder; j++) {
      const order = Math.abs(i) + Math.abs(j);
      if (order > maxOrder) continue;
      out.push({ src: { x: mirror(i, tx.x, w), y: mirror(j, tx.y, h) }, order, gain: reflectivity ** order });
    }
  return out.sort((a, b) => a.order - b.order);
}

export const dist = (a: Vec, b: Vec) => Math.hypot(a.x - b.x, a.y - b.y);

/** |H(f)| at rx for subcarrier k: sum of path phasors, amplitude ∝ gain / distance. */
export function csiAmplitude(paths: Path[], rx: Vec, k: number, extra: Path[] = []): number {
  const f = F0 + k * DF;
  let re = 0;
  let im = 0;
  for (const p of [...paths, ...extra]) {
    const d = dist(p.src, rx);
    const a = p.gain / d;
    const phi = (-2 * Math.PI * f * d) / C;
    re += a * Math.cos(phi);
    im += a * Math.sin(phi);
  }
  return Math.hypot(re, im);
}

/** Radius of the n-th Fresnel zone at distances d1, d2 from the two ends. */
export const fresnelRadius = (n: number, d1: number, d2: number, lambda = LAMBDA) =>
  Math.sqrt((n * lambda * d1 * d2) / (d1 + d2));

/** Which Fresnel zone (1-based) a point lies in, from its excess path length over line of sight. */
export const fresnelZone = (p: Vec, tx: Vec, rx: Vec, lambda = LAMBDA) =>
  Math.floor((dist(tx, p) + dist(p, rx) - dist(tx, rx)) / (lambda / 2)) + 1;
