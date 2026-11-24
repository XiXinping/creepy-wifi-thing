export type RGB = [number, number, number];

/** A design token from tokens.css as 0..255 rgb (tokens stay the single source of colour). */
export function token(name: string): RGB {
  const hex = getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim().slice(1);
  return [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16)) as RGB;
}

export const css = ([r, g, b]: RGB, a = 1) => `rgba(${r},${g},${b},${a})`;

export const mix = (a: RGB, b: RGB, t: number): RGB => [0, 1, 2].map((i) => a[i] + (b[i] - a[i]) * t) as RGB;

/** The heatmap ramp deep → wash → bloom. */
export const ramp = (deep: RGB, wash: RGB, bloom: RGB, t: number) => (t < 0.5 ? mix(deep, wash, t * 2) : mix(wash, bloom, t * 2 - 1));
