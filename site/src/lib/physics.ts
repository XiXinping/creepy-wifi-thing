// Radio physics shared by the shader, the subcarrier strip and the tests.
// Numbers match the recordings: channel 7, legacy 20 MHz OFDM (see tools/prep_csi.py).

export const C = 299_792_458;
export const F0 = 2.442e9;
export const DF = 312.5e3;
export const LAMBDA = C / F0;
export const SUBCARRIERS = Array.from({ length: 57 }, (_, i) => i - 28).filter((k) => k !== 0);

export type Vec = { x: number; y: number };
export type Path = { src: Vec; order: number; gain: number };

/** Mirror images of `tx` in a W×H rectangular room, up to `maxOrder` wall bounces. Each bounce off a wall
 *  (denser than air) flips the wave as well as weakening it: reflection coefficient −`reflectivity`. */
export function imageSources(tx: Vec, w: number, h: number, maxOrder: number, reflectivity = 0.6): Path[] {
  const mirror = (i: number, p: number, L: number) => i * L + (i % 2 === 0 ? p : L - p);
  const out: Path[] = [];
  for (let i = -maxOrder; i <= maxOrder; i++)
    for (let j = -maxOrder; j <= maxOrder; j++) {
      const order = Math.abs(i) + Math.abs(j);
      if (order > maxOrder) continue;
      out.push({ src: { x: mirror(i, tx.x, w), y: mirror(j, tx.y, h) }, order, gain: (-reflectivity) ** order });
    }
  return out.sort((a, b) => a.order - b.order);
}

export const dist = (a: Vec, b: Vec) => Math.hypot(a.x - b.x, a.y - b.y);

/** 0 when the straight line a→b runs through a circle (centre c, radius r), 1 when it clears it,
 *  blended over ±`soft` metres around the edge. The field shader repeats this for its shadow. */
export function clearance(a: Vec, b: Vec, c: Vec, r: number, soft = 0.03): number {
  const d = { x: b.x - a.x, y: b.y - a.y };
  const t = Math.min(1, Math.max(0, ((c.x - a.x) * d.x + (c.y - a.y) * d.y) / (d.x * d.x + d.y * d.y)));
  const m = dist({ x: a.x + t * d.x, y: a.y + t * d.y }, c);
  const u = Math.min(1, Math.max(0, (m - (r - soft)) / (2 * soft)));
  return u * u * (3 - 2 * u);
}

/** Where a wave tx → circle (centre c, radius r) → rx reflects: the point on the circle with the shortest
 *  path, i.e. equal angles in and out. If the circle blocks tx–rx, a point where that line meets it. */
export function specular(tx: Vec, rx: Vec, c: Vec, r: number, n = 720): Vec {
  let best = c;
  let len = Infinity;
  for (let i = 0; i < n; i++) {
    const a = (2 * Math.PI * i) / n;
    const q = { x: c.x + r * Math.cos(a), y: c.y + r * Math.sin(a) };
    const l = dist(tx, q) + dist(q, rx);
    if (l < len) [best, len] = [q, l];
  }
  return best;
}

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
