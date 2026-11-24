"""Turn the UCA-room recordings into compact arrays for the site.

Run from site/: tools/.venv/bin/python tools/prep_csi.py   (venv has numpy + matplotlib, which csi_heatmap imports)
Parsing is delegated to csi_heatmap.py so the site shows exactly what that tool shows.
Writes public/data/<name>.amp.u8, <name>.var.u8 (time-major, T x K) and public/data/csi.json.
"""
import csv
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
UTILS = ROOT / "ESP32-CSI-Tool" / "python_utils"
sys.path.insert(0, str(UTILS))
from csi_heatmap import compute_rolling_variance, decode_csv_row  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "public" / "data"
CSI_COLUMN = 25   # csi_heatmap.py default
TS_COLUMN = 26    # trailing host epoch seconds
RATE_HZ = 20      # site resolution; recordings are ~80 Hz
VAR_WINDOW_S = 0.5
MATRICES = ["empty", "still", "motion"]  # full heatmaps; every file contributes a mean profile


def load(path):
    amps, ts = [], []
    with open(path, newline="") as f:
        for row in csv.reader(f):
            dec = decode_csv_row(row, CSI_COLUMN, strip_brackets=True)
            if dec is None or len(dec[0]) != 64:
                continue
            amps.append(dec[0])
            ts.append(float(row[TS_COLUMN]))
    return np.asarray(amps), np.asarray(ts)


def downsample(m, ts, rate):
    bins = np.floor((ts - ts[0]) * rate).astype(int)
    out = np.zeros((bins[-1] + 1, m.shape[1]))
    np.add.at(out, bins, m)
    n = np.bincount(bins)[:, None]
    keep = n[:, 0] > 0
    return out[keep] / n[keep]


def main():
    files = sorted((UTILS / "data").glob("*.csv"))
    raw = {f.stem: load(f) for f in files}
    # guard + DC bins are exactly zero in every frame
    live = np.flatnonzero(np.any(np.vstack([a for a, _ in raw.values()]) != 0, axis=0))

    meta = {"rateHz": RATE_HZ, "subcarriers": live.tolist(), "profiles": {}, "matrices": {}}
    amp_max = max(np.percentile(a[:, live], 99.5) for a, _ in raw.values())
    var_all = {}
    for name, (a, ts) in raw.items():
        a = a[:, live]
        meta["profiles"][name] = np.round(a.mean(axis=0) / amp_max, 4).tolist()
        if name in MATRICES:
            hz = len(ts) / (ts[-1] - ts[0])
            var = compute_rolling_variance(a.T, max(2, int(VAR_WINDOW_S * hz))).T
            var_all[name] = (downsample(a, ts, RATE_HZ), downsample(var, ts, RATE_HZ), hz)
    var_max = max(np.percentile(v, 99.5) for _, v, _ in var_all.values())
    for name, (a, v, hz) in var_all.items():
        q = lambda x, top: np.clip(np.round(x / top * 255), 0, 255).astype(np.uint8)
        (OUT / f"{name}.amp.u8").write_bytes(q(a, amp_max).tobytes())
        # sqrt: variance spans decades; the eye reads std-dev-like scaling better
        (OUT / f"{name}.var.u8").write_bytes(q(np.sqrt(v), np.sqrt(var_max)).tobytes())
        meta["matrices"][name] = {"frames": len(a), "sourceHz": round(hz, 1)}
        print(f"{name}: {len(a)} frames @ {RATE_HZ} Hz (source {hz:.1f} Hz), K={len(live)}")
    (OUT / "csi.json").write_text(json.dumps(meta))


if __name__ == "__main__":
    main()
