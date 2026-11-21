#!/usr/bin/env python3
"""
csi_live_plot.py – Real-time / replay CSI amplitude & phase plot.

Input modes (pick ONE):
  • Serial port :  python csi_live_plot.py /dev/ttyUSB0
  • Stdin pipe  :  idf.py monitor | python csi_live_plot.py --stdin
  • CSV replay  :  python csi_live_plot.py --csv data.csv

CSV replay respects the appended Unix timestamp column
(produced by csi_to_csv.py).  Use --replay-speed to control pace:
    1.0 = original real-time rate
    2.0 = twice as fast
    0   = as fast as possible (no sleep)
"""

import sys
import argparse
import math
import time
import csv
import collections

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

try:
    import serial
except ImportError:
    serial = None


# ── WaitTimer ────────────────────────────────────────────────────────────────


class WaitTimer:
    def __init__(self, period):
        self.period = period
        self.next = time.monotonic() + period

    def check(self):
        return time.monotonic() >= self.next

    def update(self):
        self.next = time.monotonic() + self.period


# ── CLI ──────────────────────────────────────────────────────────────────────


def parse_args():
    p = argparse.ArgumentParser(
        description="CSI amplitude/phase live plot (serial | stdin | CSV replay).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ── input mode ──
    p.add_argument(
        "input",
        nargs="?",
        default=None,
        help="Serial port path OR CSV file path (see --stdin / --csv)",
    )
    p.add_argument(
        "--stdin", action="store_true", help="Read CSI lines from stdin (pipe mode)"
    )
    p.add_argument(
        "--csv",
        type=str,
        default=None,
        metavar="FILE",
        help="Replay from a CSI CSV file instead of live input",
    )

    # ── serial options ──
    p.add_argument(
        "--baud", type=int, default=115200, help="Baud rate (default 115200)"
    )
    p.add_argument(
        "--timeout", type=float, default=1.0, help="Serial read timeout in seconds"
    )

    # ── parsing ──
    p.add_argument(
        "--csi-index",
        type=int,
        default=25,
        help="Column index (0-based) holding the CSI blob "
        "(default 25, ESP32 idf.py monitor format)",
    )
    p.add_argument(
        "--no-brackets",
        action="store_true",
        help="CSI blob has NO [ ] brackets (already CSV-clean)",
    )

    # ── replay ──
    p.add_argument(
        "--replay-speed",
        type=float,
        default=1.0,
        metavar="FACTOR",
        help="Replay speed: 1=realtime, 2=2×, 0=max speed "
        "(default 1).  Ignored for live modes.",
    )
    p.add_argument(
        "--ts-col",
        type=int,
        default=-1,
        help="Column index for the timestamp in the CSV (default -1 = last column)",
    )

    # ── plot ──
    p.add_argument(
        "--subcarrier",
        type=int,
        default=44,
        help="Subcarrier index to plot (default 44)",
    )
    p.add_argument(
        "--window",
        type=int,
        default=100,
        help="Sliding-window size in packets (default 100)",
    )
    p.add_argument(
        "--render-rate",
        type=float,
        default=5.0,
        help="Plot refresh rate in Hz (default 5)",
    )
    p.add_argument(
        "--stats-rate",
        type=float,
        default=1.0,
        help="Stats print interval in seconds (default 1)",
    )
    p.add_argument(
        "--no-phase",
        action="store_true",
        help="Only show the amplitude panel (single plot)",
    )

    return p.parse_args()


# ── Input readers ────────────────────────────────────────────────────────────


def make_serial_reader(args):
    if serial is None:
        print("pyserial not installed. Use --stdin or --csv mode.", file=sys.stderr)
        sys.exit(1)
    try:
        ser = serial.Serial(args.input, baudrate=args.baud, timeout=args.timeout)
    except serial.SerialException as e:
        print(f"Error opening '{args.input}': {e}", file=sys.stderr)
        sys.exit(1)

    def rl():
        while True:
            try:
                raw = ser.readline()
                if raw:
                    return raw.decode("utf-8", errors="replace").strip()
                return ""
            except Exception:
                pass

    return rl, ser.close


def make_stdin_reader(args):
    def rl():
        try:
            line = sys.stdin.readline()
            return line.rstrip("\n\r") if line else ""
        except Exception:
            return ""

    return rl, lambda: None


def make_csv_reader(args):
    """
    Return (row_iter_fn, close_fn).
    Each call to row_iter_fn returns a list[str] (one CSV row) or None at EOF.
    """
    try:
        fh = open(args.csv, newline="")
    except FileNotFoundError:
        print(f"CSV file not found: {args.csv}", file=sys.stderr)
        sys.exit(1)

    reader = csv.reader(fh)
    iterator = iter(reader)

    def rl():
        try:
            return next(iterator)
        except StopIteration:
            return None

    return rl, fh.close


def build_reader(args):
    if args.csv:
        return make_csv_reader(args)
    if args.stdin:
        return make_stdin_reader(args)
    if args.input:
        # auto-detect: if the arg looks like a CSV path, use CSV mode
        if args.input.lower().endswith(".csv"):
            args.csv = args.input
            return make_csv_reader(args)
        return make_serial_reader(args)
    print(
        "No input given. Provide a serial port, use --stdin, or use --csv FILE.",
        file=sys.stderr,
    )
    sys.exit(1)


# ── First-line skip (flash / header) ────────────────────────────────────────


def wait_for_first_csi(readline_fn, is_csv):
    """
    For live modes: skip flash / debug lines until CSI_DATA appears.
    For CSV mode: optionally skip a header row if it doesn't parse.
    Returns the first useful line (str for live, list[str] for CSV).
    """
    if is_csv:
        return None  # no special skip needed; we try-parse every row
    print("Waiting for first CSI_DATA line …", file=sys.stderr)
    while True:
        line = readline_fn()
        if line is None:
            print("Stream ended before CSI_DATA was seen.", file=sys.stderr)
            sys.exit(1)
        if line == "":
            continue
        if "CSI_DATA" in line:
            print("CSI stream started.", file=sys.stderr)
            return line


# ── CSI parsing (shared) ────────────────────────────────────────────────────


def parse_csi_blob(blob, no_brackets=False):
    """
    Parse a CSI real/imaginary blob into (amplitudes, phases).
    Accepts:  "[r0,i0,r1,i1, ...]"  (brackets)  OR  "r0 i0 r1 i1 ..."  (no_brackets)
    Returns (amp_list, phase_list) or (None, None).
    """
    if not no_brackets:
        blob = blob.replace("[", "").replace("]", "").strip()
    tokens = blob.split()
    if len(tokens) < 2:
        return None, None

    vals = []
    for t in tokens:
        try:
            vals.append(int(t))
        except ValueError:
            try:
                vals.append(float(t))
            except ValueError:
                continue

    if len(vals) < 2:
        return None, None

    imaginary = vals[0::2]
    real = vals[1::2]
    n = min(len(imaginary), len(real))

    amps = [math.sqrt(imaginary[j] ** 2 + real[j] ** 2) for j in range(n)]
    phs = [math.atan2(imaginary[j], real[j]) for j in range(n)]
    return amps, phs


def parse_live_line(line, csi_index):
    """
    Parse a live CSI_DATA line (comma-separated, CSI blob at csi_index).
    Returns (amps, phases) or (None, None).
    """
    try:
        parts = line.split(",")
        if csi_index >= len(parts):
            return None, None
        blob = parts[csi_index].strip()
    except (IndexError, ValueError):
        return None, None
    return parse_csi_blob(blob)


def parse_csv_row(row, csi_index, ts_col):
    """
    Parse a CSV row.  The CSI blob is at csi_index; the timestamp
    is at ts_col (default: last column).
    Returns (amps, phases, timestamp) or (None, None, None).
    """
    if len(row) <= max(csi_index, abs(ts_col)):
        return None, None, None

    ts_val = None
    try:
        ts_val = float(row[ts_col])
    except (ValueError, IndexError):
        pass

    blob = row[csi_index].strip()
    amps, phs = parse_csi_blob(blob, no_brackets=True)
    return amps, phs, ts_val


# ── Plotting ─────────────────────────────────────────────────────────────────


def init_figure(show_phase):
    plt.ion()
    if show_phase:
        fig = plt.figure(figsize=(11, 8))
        ax_amp = fig.add_subplot(211)
        ax_phs = fig.add_subplot(212, sharex=ax_amp)
    else:
        fig = plt.figure(figsize=(11, 5))
        ax_amp = fig.add_subplot(111)
        ax_phs = None
    fig.tight_layout()
    fig.canvas.draw()
    plt.show(block=False)
    return fig, ax_amp, ax_phs


def render_plot(fig, ax_amp, ax_phs, perm_amp, perm_phase, subcarrier, window):
    if len(perm_amp) < 2:
        return

    df_amp = np.asarray(perm_amp, dtype=np.float64)
    df_phs = np.asarray(perm_phase, dtype=np.float64) if ax_phs else None

    n = len(df_amp)
    xs = np.arange(window - n, window)

    sc = min(subcarrier, df_amp.shape[1] - 1)

    # ── amplitude ──
    ax_amp.clear()
    ax_amp.plot(xs, df_amp[:, sc], color="r", linewidth=0.9)
    ax_amp.set_ylabel("Amplitude")
    ax_amp.set_title(f"Amplitude – subcarrier {sc}   (window {window}, {n} pkts)")
    ax_amp.set_xlim(0, window)
    ax_amp.grid(True, alpha=0.25)

    # ── phase ──
    if ax_phs and df_phs is not None:
        sc_p = min(subcarrier, df_phs.shape[1] - 1)
        ax_phs.clear()
        ax_phs.plot(xs, df_phs[:, sc_p], color="b", linewidth=0.9)
        ax_phs.set_xlabel("Packet index (sliding window)")
        ax_phs.set_ylabel("Phase (rad)")
        ax_phs.set_title(f"Phase – subcarrier {sc_p}")
        ax_phs.set_xlim(0, window)
        ax_phs.grid(True, alpha=0.25)

    fig.canvas.flush_events()
    plt.pause(0.001)


# ── main ─────────────────────────────────────────────────────────────────────


def main():
    args = parse_args()
    is_csv = args.csv is not None
    readline_fn, close_fn = build_reader(args)

    # skip flash / debug header in live mode
    if not is_csv:
        first = wait_for_first_csi(readline_fn, is_csv=False)
    else:
        first = None

    perm_amp = collections.deque(maxlen=args.window)
    perm_phase = collections.deque(maxlen=args.window)

    print_timer = WaitTimer(args.stats_rate)
    plot_timer = WaitTimer(1.0 / args.render_rate)

    total_packets = 0
    pkt_this_sec = 0

    show_phase = not args.no_phase
    fig, ax_amp, ax_phs = init_figure(show_phase)

    # ── seed with first line (live mode) ──
    if first is not None:
        amps, phs = parse_live_line(first, args.csi_index)
        if amps:
            perm_amp.append(amps)
            perm_phase.append(phs)
            total_packets += 1
            pkt_this_sec += 1

    # ── replay timing bookkeeping (CSV mode) ──
    prev_ts = None
    replay_speed = args.replay_speed

    try:
        while True:
            row = readline_fn()
            if row is None:
                # EOF (CSV) or serial closed
                if is_csv:
                    print("CSV replay finished.", file=sys.stderr)
                break
            if isinstance(row, str):
                # ── live / stdin mode ──
                line = row
                if not line:
                    continue
                if "CSI_DATA" not in line:
                    continue
                amps, phs = parse_live_line(line, args.csi_index)
                ts = None
            else:
                # ── CSV replay mode ──
                amps, phs, ts = parse_csv_row(row, args.csi_index, args.ts_col)

            if amps is None:
                continue

            perm_amp.append(amps)
            perm_phase.append(phs)
            total_packets += 1
            pkt_this_sec += 1

            # ── periodic stats ──
            if print_timer.check():
                print_timer.update()
                label = "replay" if is_csv else "live"
                print(
                    f"[{label}] {pkt_this_sec:>4d} pkt/s   total: {total_packets:>8d}",
                    file=sys.stderr,
                )
                pkt_this_sec = 0

            # ── throttled render ──
            if plot_timer.check() and len(perm_amp) >= 2:
                plot_timer.update()
                render_plot(
                    fig,
                    ax_amp,
                    ax_phs,
                    perm_amp,
                    perm_phase,
                    args.subcarrier,
                    args.window,
                )

            # ── CSV replay: honour timestamps ──
            if is_csv and ts is not None and replay_speed > 0:
                if prev_ts is not None:
                    delta = (ts - prev_ts) / replay_speed
                    if delta > 0:
                        time.sleep(delta)
                prev_ts = ts

    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
    finally:
        close_fn()
        mode = "CSV replay" if is_csv else "live"
        print(f"[{mode}] Total packets processed: {total_packets}", file=sys.stderr)
        plt.close(fig)


if __name__ == "__main__":
    main()
