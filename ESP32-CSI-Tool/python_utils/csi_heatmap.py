#!/usr/bin/env python3
"""
csi_heatmap.py — Heatmap of CSI amplitude / phase / amplitude-variance
over time × subcarrier.

Input:  CSV file, serial port, or stdin pipe.
Output: stacked matplotlib panels (amplitude, phase, and/or rolling variance).

Examples:
    python csi_heatmap.py csi_data.csv
    python csi_heatmap.py --csv csi_data.csv --display amplitude variance
    python csi_heatmap.py --live /dev/ttyACM1 --subcarrier-range 10:50
    idf.py monitor | python csi_heatmap.py --stdin --csi-column 25 --live
"""

from __future__ import annotations

import sys
import argparse
import csv
import math
import time
import collections
from typing import Iterator

import numpy as np
import matplotlib
import matplotlib.pyplot as plt

try:
    import serial as pyserial
except ImportError:
    pyserial = None


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════


def parse_args():
    p = argparse.ArgumentParser(
        description="CSI heatmap (amplitude / phase / amplitude-variance)."
    )

    # Input
    p.add_argument(
        "input", nargs="?", help="CSV path or serial port (omit when using --stdin)"
    )
    p.add_argument(
        "--csv",
        default=None,
        metavar="FILE",
        help="Explicit CSV path (backwards-compat alias)",
    )
    p.add_argument(
        "--stdin", action="store_true", help="Read lines from stdin (pipe mode)"
    )
    p.add_argument(
        "--live",
        action="store_true",
        help="Force live (streaming) mode even for a .csv input",
    )

    # Serial
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--serial-timeout", type=float, default=1.0)

    # Parsing
    p.add_argument(
        "--csi-column",
        type=int,
        default=25,
        help="Comma-separated column holding the CSI blob",
    )
    p.add_argument(
        "--no-brackets", action="store_true", help="CSI blob is already bracket-free"
    )
    p.add_argument(
        "--timestamp-column",
        type=int,
        default=-1,
        help="Timestamp column index in CSV (default: last)",
    )

    # Display
    p.add_argument(
        "--display",
        nargs="+",
        choices=["amplitude", "phase", "variance"],
        default=["amplitude", "phase"],
        help="Panels to show. 'variance' = rolling variance of amplitude",
    )
    p.add_argument(
        "--variance-window",
        type=int,
        default=20,
        help="Window (packets) used for rolling variance (default 20)",
    )

    p.add_argument(
        "--time-window",
        type=float,
        default=15.0,
        help="Live view length in seconds (default 15)",
    )
    p.add_argument(
        "--max-packets",
        type=int,
        default=0,
        help="Static CSV: stop after N packets (0 = all)",
    )
    p.add_argument(
        "--render-rate",
        type=float,
        default=10.0,
        help="Live refresh rate Hz (default 10)",
    )

    p.add_argument(
        "--subcarrier-range",
        default=None,
        metavar="LO:HI",
        help="Restrict subcarriers to [lo, hi] inclusive",
    )

    p.add_argument("--amplitude-colormap", default="plasma")
    p.add_argument("--phase-colormap", default="twilight")
    p.add_argument("--variance-colormap", default="inferno")
    p.add_argument("--vmin-amp", type=float, default=None)
    p.add_argument("--vmax-amp", type=float, default=None)
    p.add_argument("--vmin-phase", type=float, default=-math.pi)
    p.add_argument("--vmax-phase", type=float, default=math.pi)
    p.add_argument("--vmin-var", type=float, default=None)
    p.add_argument("--vmax-var", type=float, default=None)

    p.add_argument("--save", default=None, help="Save PNG/PDF (static mode only)")
    p.add_argument("--dpi", type=int, default=200)
    p.add_argument("--title", default="CSI Heatmap")

    return p.parse_args()


# ═══════════════════════════════════════════════════════════════════════════
# Input sources — each returns an iterator
#   CSV  yields list[str]
#   live yields str
# ═══════════════════════════════════════════════════════════════════════════


def open_csv_source(path: str) -> Iterator[list[str]]:
    fh = open(path, newline="")
    try:
        yield from csv.reader(fh)
    finally:
        fh.close()


def open_stdin_source() -> Iterator[str]:
    while True:
        line = sys.stdin.readline()
        if not line:
            return
        yield line.rstrip("\n\r")


def open_serial_source(port: str, baud: int, timeout: float) -> Iterator[str]:
    if pyserial is None:
        raise RuntimeError("pyserial is not installed (pip install pyserial)")
    ser = pyserial.Serial(port, baudrate=baud, timeout=timeout)
    try:
        while True:
            raw = ser.readline()
            if raw:
                yield raw.decode("utf-8", errors="replace").strip()
    finally:
        ser.close()


def open_input_source(args) -> tuple[Iterator, bool]:
    """Returns (iterator, is_csv)."""
    if args.stdin:
        return open_stdin_source(), False
    path = args.csv or args.input
    if not path:
        raise SystemExit("No input given. Pass a .csv path, a serial port, or --stdin.")
    if path.lower().endswith(".csv") and not args.live:
        return open_csv_source(path), True
    if path.lower().endswith(".csv") and args.live:
        # replay CSV through the live pipeline
        return open_csv_source(path), True
    return open_serial_source(path, args.baud, args.serial_timeout), False


# ═══════════════════════════════════════════════════════════════════════════
# CSI blob → numbers (pure parsing, no math on amplitudes/phases)
# ═══════════════════════════════════════════════════════════════════════════


def extract_blob_from_line(line: str, column: int) -> str | None:
    parts = line.split(",")
    return parts[column].strip() if 0 <= column < len(parts) else None


def extract_blob_from_row(row: list[str], column: int) -> str | None:
    return row[column].strip() if 0 <= column < len(row) else None


def tokenize_blob(blob: str, strip_brackets: bool) -> list[float]:
    if strip_brackets:
        blob = blob.replace("[", "").replace("]", "")
    out: list[float] = []
    for tok in blob.split():
        try:
            out.append(float(tok))
        except ValueError:
            continue
    return out


def split_into_real_imaginary(
    values: list[float],
) -> tuple[list[float], list[float]] | None:
    """ESP32 CSI order is imag,real,imag,real,... Return (real, imag)."""
    if len(values) < 2:
        return None
    imag = values[0::2]
    real = values[1::2]
    n = min(len(real), len(imag))
    return real[:n], imag[:n]


# ═══════════════════════════════════════════════════════════════════════════
# DSP — separate functions for amplitude and phase
# ═══════════════════════════════════════════════════════════════════════════


def compute_amplitudes(real: list[float], imag: list[float]) -> list[float]:
    n = min(len(real), len(imag))
    return [math.hypot(real[j], imag[j]) for j in range(n)]


def compute_phases(real: list[float], imag: list[float]) -> list[float]:
    n = min(len(real), len(imag))
    return [math.atan2(imag[j], real[j]) for j in range(n)]


def decode_csi_blob(
    blob: str, strip_brackets: bool
) -> tuple[list[float], list[float]] | None:
    values = tokenize_blob(blob, strip_brackets)
    pair = split_into_real_imaginary(values)
    if pair is None:
        return None
    real, imag = pair
    return compute_amplitudes(real, imag), compute_phases(real, imag)


def decode_live_line(
    line: str, column: int, strip_brackets: bool
) -> tuple[list, list] | None:
    if "CSI_DATA" not in line:
        return None
    blob = extract_blob_from_line(line, column)
    if blob is None:
        return None
    return decode_csi_blob(blob, strip_brackets)


def decode_csv_row(
    row: list[str], column: int, strip_brackets: bool
) -> tuple[list, list] | None:
    blob = extract_blob_from_row(row, column)
    if blob is None:
        return None
    return decode_csi_blob(blob, strip_brackets)


# ═══════════════════════════════════════════════════════════════════════════
# Rolling variance (per subcarrier)
# ═══════════════════════════════════════════════════════════════════════════


def compute_rolling_variance(matrix: np.ndarray, window: int) -> np.ndarray:
    """
    matrix: shape (K, T). Returns (K, T) where column t is the variance of
    the previous min(window, t+1) samples along the time axis.
    """
    K, T = matrix.shape
    out = np.zeros((K, T), dtype=np.float64)
    cs = np.cumsum(matrix, axis=1)
    cs2 = np.cumsum(matrix**2, axis=1)
    for t in range(T):
        w = min(window, t + 1)
        start = t - w
        if start < 0:
            s = cs[:, t]
            s2 = cs2[:, t]
        else:
            s = cs[:, t] - cs[:, start]
            s2 = cs2[:, t] - cs2[:, start]
        mean = s / w
        out[:, t] = np.maximum(s2 / w - mean**2, 0.0)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Sliding time-window buffer
# ═══════════════════════════════════════════════════════════════════════════


class CsiBuffer:
    def __init__(self, time_window: float):
        self.time_window = time_window
        self.amps: collections.deque = collections.deque()
        self.phs: collections.deque = collections.deque()
        self.ts: collections.deque = collections.deque()
        self.K: int | None = None

    def push(self, amps, phs, ts) -> bool:
        if self.K is None:
            self.K = len(amps)
        elif len(amps) != self.K or len(phs) != self.K:
            return False
        self.amps.append(np.asarray(amps, dtype=np.float64))
        self.phs.append(np.asarray(phs, dtype=np.float64))
        self.ts.append(float(ts))
        while self.ts and self.ts[0] < ts - self.time_window:
            self.amps.popleft()
            self.phs.popleft()
            self.ts.popleft()
        return True

    def is_ready(self) -> bool:
        return len(self.amps) >= 2

    def subcarrier_bounds(self, lo, hi) -> tuple[int, int]:
        if self.K is None:
            return 0, 0
        if lo is None:
            return 0, self.K - 1
        return max(0, min(lo, self.K - 1)), max(0, min(hi, self.K - 1))

    def matrix(self, kind: str, lo: int, hi: int, var_window: int = 0) -> np.ndarray:
        src = self.amps if kind in ("amplitude", "variance") else self.phs
        m = np.column_stack(list(src))
        m = m[lo : hi + 1, :]
        if kind == "variance":
            m = compute_rolling_variance(m, var_window)
        return m

    def x_extent_live(self) -> tuple[float, float]:
        t = list(self.ts)
        return t[0] - t[-1], 0.0

    def x_extent_static(self) -> tuple[float, float]:
        t = list(self.ts)
        if len(t) < 2:
            return 0.0, 1.0
        return t[0], t[-1]


# ═══════════════════════════════════════════════════════════════════════════
# Rendering
# ═══════════════════════════════════════════════════════════════════════════

DISPLAY_CONFIG = {
    "amplitude": dict(
        cmap_attr="amplitude_colormap",
        vmin_attr="vmin_amp",
        vmax_attr="vmax_amp",
        label="Amplitude",
        title="Amplitude",
    ),
    "phase": dict(
        cmap_attr="phase_colormap",
        vmin_attr="vmin_phase",
        vmax_attr="vmax_phase",
        label="Phase (rad)",
        title="Phase",
    ),
    "variance": dict(
        cmap_attr="variance_colormap",
        vmin_attr="vmin_var",
        vmax_attr="vmax_var",
        label="Variance of amplitude",
        title="Amplitude variance (rolling)",
    ),
}


def build_figure(display_modes):
    n = len(display_modes)
    fig, axes = plt.subplots(n, 1, figsize=(12, 4.5 * n), sharex=True)
    if n == 1:
        axes = [axes]
    return fig, axes


def draw_or_update(image, ax, matrix, extent, mode, args, fig):
    cfg = DISPLAY_CONFIG[mode]
    if image is None:
        image = ax.imshow(
            matrix,
            origin="lower",
            aspect="auto",
            extent=extent,
            cmap=getattr(args, cfg["cmap_attr"]),
            vmin=getattr(args, cfg["vmin_attr"]),
            vmax=getattr(args, cfg["vmax_attr"]),
        )
        ax.set_ylabel("Subcarrier index")
        ax.set_title(cfg["title"])
        cbar = fig.colorbar(image, ax=ax, pad=0.02)
        cbar.set_label(cfg["label"])
        return image, cbar
    image.set_data(matrix)
    image.set_extent(extent)
    return image, None


def finalise_axes(ax, y_lo, y_hi, first_axis, x_lo, x_hi):
    ax.set_xlim(x_lo, x_hi)
    ax.set_ylim(y_lo, y_hi + 1)
    if first_axis:
        ax.set_xlabel("Time (s, 0 = now)")


# ═══════════════════════════════════════════════════════════════════════════
# Live loop
# ═══════════════════════════════════════════════════════════════════════════


def run_live(args):
    plt.ion()  # interactive, only for live
    source, is_csv = open_input_source(args)
    strip_brackets = is_csv or args.no_brackets
    buf = CsiBuffer(args.time_window)

    sc_lo, sc_hi = (None, None)
    if args.subcarrier_range:
        sc_lo, sc_hi = (int(x) for x in args.subcarrier_range.split(":"))

    fig, axes = build_figure(args.display)
    images = {m: None for m in args.display}

    total = 0
    render_period = max(1, int(round(1.0 / args.render_rate)))

    try:
        for row in source:
            if isinstance(row, str):
                decoded = decode_live_line(row, args.csi_column, strip_brackets)
                ts = time.time()
            else:
                decoded = decode_csv_row(row, args.csi_column, strip_brackets)
                try:
                    ts = float(row[args.timestamp_column])
                except (ValueError, IndexError):
                    ts = time.time()

            if decoded is None:
                continue
            amps, phs = decoded
            if not buf.push(amps, phs, ts):
                continue
            total += 1

            if not buf.is_ready() or total % render_period:
                continue

            y_lo, y_hi = buf.subcarrier_bounds(sc_lo, sc_hi)
            x_lo, x_hi = buf.x_extent_live()
            extent = (x_lo, x_hi, y_lo, y_hi + 1)

            for idx, mode in enumerate(args.display):
                m = buf.matrix(mode, y_lo, y_hi, args.variance_window)
                image, _ = draw_or_update(
                    images[mode], axes[idx], m, extent, mode, args, fig
                )
                images[mode] = image
                finalise_axes(
                    axes[idx],
                    y_lo,
                    y_hi,
                    first_axis=(idx == len(args.display) - 1),
                    x_lo=-args.time_window,
                    x_hi=0.0,
                )

            fig.suptitle(
                f"{args.title} — last {args.time_window:.0f} s, "
                f"sc {y_lo}–{y_hi}, total={total}"
            )
            fig.canvas.flush_events()
            plt.pause(0.01)

    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
    finally:
        print(f"Total packets: {total}", file=sys.stderr)


# ═══════════════════════════════════════════════════════════════════════════
# Static (CSV) loop
# ═══════════════════════════════════════════════════════════════════════════


def run_static(args):
    source, is_csv = open_input_source(args)
    strip_brackets = is_csv or args.no_brackets
    buf = CsiBuffer(time_window=float("inf"))

    sc_lo, sc_hi = (None, None)
    if args.subcarrier_range:
        sc_lo, sc_hi = (int(x) for x in args.subcarrier_range.split(":"))

    total = 0
    for row in source:
        decoded = decode_csv_row(row, args.csi_column, strip_brackets)
        if decoded is None:
            continue
        try:
            ts = float(row[args.timestamp_column])
        except (ValueError, IndexError):
            ts = float(total)
        if buf.push(*decoded, ts):
            total += 1
        if args.max_packets and total >= args.max_packets:
            break

    print(f"Loaded {total} packets", file=sys.stderr)
    if total == 0:
        raise SystemExit(
            "No CSI rows parsed. Check --csi-column (expected 0-based index of "
            "the bracketed blob) and whether your CSV uses brackets."
        )

    fig, axes = build_figure(args.display)
    y_lo, y_hi = buf.subcarrier_bounds(sc_lo, sc_hi)
    x_lo, x_hi = buf.x_extent_static()
    t = list(buf.ts)
    extent = (x_lo, x_hi, y_lo, y_hi + 1)

    for idx, mode in enumerate(args.display):
        m = buf.matrix(mode, y_lo, y_hi, args.variance_window)
        draw_or_update(None, axes[idx], m, extent, mode, args, fig)
        axes[idx].set_xlim(x_lo, x_hi)
        axes[idx].set_ylim(y_lo, y_hi + 1)
        if idx == len(args.display) - 1:
            axes[idx].set_xlabel("Time (s)" if t[-1] > 1e9 else "Packet index")

    fig.suptitle(f"{args.title}  —  sc {y_lo}–{y_hi}, {total} packets")
    fig.tight_layout()

    if args.save:
        fig.savefig(args.save, dpi=args.dpi)
        print(f"Saved {args.save}", file=sys.stderr)
    else:
        plt.show(block=True)


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════


def main():
    args = parse_args()
    path = args.csv or args.input
    if args.stdin or args.live or (path and not path.lower().endswith(".csv")):
        run_live(args)
    else:
        run_static(args)


if __name__ == "__main__":
    main()
