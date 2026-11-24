// Build-time only: list which render frames exist so the client never requests a missing one.
import fs from "node:fs";
import path from "node:path";
import type { Frames } from "./sequence";
import type { Rise } from "./room";

const dir = path.join(process.cwd(), "public", "renders");

const json = (f: string) => (fs.existsSync(f) ? JSON.parse(fs.readFileSync(f, "utf8")) : null);

export function frames(shot: string): Frames | null {
  const list = (size: string) => {
    const d = path.join(dir, shot, size);
    if (!fs.existsSync(d)) return [];
    return fs
      .readdirSync(d)
      .filter((f) => /^\d{4}\.webp$/.test(f))
      .map((f) => parseInt(f, 10))
      .sort((a, b) => a - b);
  };
  const lg = list("lg");
  const sm = list("sm");
  if (!lg.length && !sm.length) return null;
  const count = json(path.join(dir, `${shot}.frames.json`))?.count ?? Math.max(...lg, ...sm) + 1;
  return { count, lg, sm };
}

// Required: the physics stage is laid out from it.
export const rise = (): Rise => JSON.parse(fs.readFileSync(path.join(dir, "rise.json"), "utf8"));
