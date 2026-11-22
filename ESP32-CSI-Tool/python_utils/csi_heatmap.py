#!/usr/bin/env python3
"""
csi_heatmap.py – 2-D heatmap of CSI amplitude over time × subcarrier.

  X axis : time (packet index, or actual clock if CSV has timestamps)
  Y axis : subcarrier index
  Color  : amplitude

Usage:
    python csi_heatmap.py csi_data.csv
    python csi_heatmap.py csi_data.csv --subcarrier-range 10:50 --max-packets 500
    python csi_heatmap.py --stdin --csi-index 25
    python csi_heatmap.py /dev/ttyUSB0 --live
    idf.py monitor | python csi_heatmap.py --stdin --csi-index 25 --live
"""

import sys
import argparse
import math
import time
import csv
import numpy as np

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors


# ── CLI ──────────────────────────────────────────────────────────────────────


def parse_args():
    p = argparse.ArgumentParser(
        description="CSI amplitude heatmap (time × subcarrier × color).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "input",
        nargs="?",
        default=None,
        help="CSV file path or serial port (or use --stdin)",
    )
    p.add_argument("--stdin", action="store_true", help="Read from stdin (pipe mode)")
    p.add_argument(
        "--live",
        action="store_true",
        help="Live-updating heatmap (requires --stdin or serial port)",
    )

    # serial
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--timeout", type=float, default=1.0)

    # parsing
    p.add_argument(
        "--csi-index",
        type=int,
        default=25,
        help="Column index of CSI blob (default 25)",
    )
    p.add_argument(
        "--no-brackets",
        action="store_true",
        help="CSI blob has no [ ] brackets (CSV mode)",
    )

    # CSV
    p.add_argument(
        "--ts-col",
        type=int,
        default=-1,
        help="Timestamp column index (default: last col)",
    )
    p.add_argument(
        "--max-packets", type=int, default=0, help="Max packets to read (0 = all)"
    )

    # plot
    p.add_argument(
        "--subcarrier-range",
        type=str,
        default=None,
        help="Subcarrier range as 'lo:hi' (default: all)",
    )
    p.add_argument(
        "--max-time-pkts",
        type=int,
        default=0,
        help="Max packets visible on x-axis (default 300)",
    )
    p.add_argument(
        "--colormap",
        type=str,
        default="plasma",
        help="Matplotlib colormap (default 'plasma'). "
        "Try: jet, inferno, viridis, hot, coolwarm, RdYlBu",
    )
    p.add_argument(
        "--vmin",
        type=float,
        default=None,
        help="Colorbar lower bound (auto if omitted)",
    )
    p.add_argument(
        "--vmax",
        type=float,
        default=None,
        help="Colorbar upper bound (auto if omitted)",
    )
    p.add_argument(
        "--normalize",
        action="store_true",
        help="Use log normalization for color mapping",
    )
    p.add_argument(
        "--save", type=str, default=None, help="Save figure to file (e.g. heatmap.png)"
    )
    p.add_argument("--dpi", type=int, default=200)
    p.add_argument("--title", type=str, default="CSI Amplitude Heatmap")
    p.add_argument(
        "--aspect",
        type=str,
        default="auto",
        help="Aspect ratio: 'auto', 'equal', or a number",
    )
    p.add_argument(
        "--time-window",
        type=float,
        default=15.0,
        metavar="SEC",
        help="Live view: how many seconds of data to keep on the x-axis (default 15)",
    )
    p.add_argument(
        "--render-rate",
        type=float,
        default=10.0,
        help="Live heatmap refresh rate in Hz (default 10)",
    )

    return p.parse_args()


# ── Readers ──────────────────────────────────────────────────────────────────


def build_reader(args):
    """Return (read_fn, close_fn, is_csv)."""
    if args.stdin:

        def rl():
            try:
                return sys.stdin.readline().rstrip("\n\r")
            except Exception:
                return None

        return rl, lambda: None, False

    if args.input:
        # CSV?
        if args.input.lower().endswith(".csv"):
            try:
                fh = open(args.input, newline="")
            except FileNotFoundError:
                print(f"File not found: {args.input}", file=sys.stderr)
                sys.exit(1)
            it = iter(csv.reader(fh))

            def rl():
                try:
                    return next(it)
                except StopIteration:
                    return None

            return rl, fh.close, True
        else:
            # serial
            try:
                import serial as sp
            except ImportError:
                print("pyserial not installed.", file=sys.stderr)
                sys.exit(1)
            try:
                ser = sp.Serial(args.input, baudrate=args.baud, timeout=args.timeout)
            except sp.SerialException as e:
                print(f"Error opening port: {e}", file=sys.stderr)
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

            return rl, ser.close, False

    print("Provide a CSV file, serial port, or use --stdin.", file=sys.stderr)
    sys.exit(1)


# ── CSI parsing ─────────────────────────────────────────────────────────────


def parse_csi_blob(blob, no_brackets=False):
    """Parse '[r0 i0 r1 i1 ...]' or 'r0 i0 r1 i1 ...' → amplitude list."""
    if not no_brackets:
        blob = blob.replace("[", "").replace("]", "").strip()
    tokens = blob.split()
    if len(tokens) < 2:
        return None
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
        return None
    imaginary = vals[0::2]
    real = vals[1::2]
    n = min(len(imaginary), len(real))
    return [math.sqrt(imaginary[j] ** 2 + real[j] ** 2) for j in range(n)]


def parse_line(line, csi_index):
    """Parse a raw 'CSI_DATA,field,field,...,blob,...' line."""
    parts = line.split(",")
    if csi_index >= len(parts):
        return None
    return parse_csi_blob(parts[csi_index].strip())


def parse_csv_row(row, csi_index, no_brackets=True):
    """Parse a CSV row (list of strings)."""
    if len(row) <= csi_index:
        return None
    return parse_csi_blob(row[csi_index].strip(), no_brackets=no_brackets)


# ── Collect data ────────────────────────────────────────────────────────────


def collect_data(args):
    """
    Read all (or --max-packets) CSI packets and return:
        amps   : np.ndarray shape (K, T)  – K subcarriers × T time-packets
        ts     : np.ndarray shape (T,)    – timestamps (or packet indices)
    """
    read_fn, close_fn, is_csv = build_reader(args)
    no_brackets = is_csv or args.no_brackets

    amps_list = []
    ts_list = []
    total = 0

    try:
        while True:
            row = read_fn()
            if row is None:
                break

            if isinstance(row, str):
                if not row or "CSI_DATA" not in row:
                    continue
                amps = parse_line(row, args.csi_index)
                t = total  # packet index as time
            else:
                amps = parse_csv_row(row, args.csi_index, no_brackets)
                try:
                    t = float(row[args.ts_col])
                except (ValueError, IndexError):
                    t = total

            if amps is None or len(amps) == 0:
                continue

            amps_list.append(amps)
            ts_list.append(t)
            total += 1

            if args.max_packets > 0 and total >= args.max_packets:
                break

    finally:
        close_fn()

    if not amps_list:
        print("No CSI data found.", file=sys.stderr)
        sys.exit(1)

    # ensure uniform length (pad/truncate to most common)
    lengths = [len(a) for a in amps_list]
    K = max(lengths)
    amps_matrix = np.zeros((K, len(amps_list)), dtype=np.float64)
    for t_i, a in enumerate(amps_list):
        amps_matrix[: len(a), t_i] = a

    ts_arr = np.array(ts_list, dtype=np.float64)
    return amps_matrix, ts_arr


# ── Plotting ─────────────────────────────────────────────────────────────────


def plot_heatmap(amps, ts, args):
    """
    amps : shape (K, T)
    ts   : shape (T,)
    """
    K, T = amps.shape

    # subcarrier range
    if args.subcarrier_range:
        lo, hi = (int(x) for x in args.subcarrier_range.split(":"))
        lo = max(0, min(lo, K - 1))
        hi = max(0, min(hi, K - 1))
    else:
        lo, hi = 0, K - 1

    amps = amps[lo : hi + 1, :]
    K_plot = hi - lo + 1

    # limit time window
    if args.max_time_pkts > 0 and T > args.max_time_pkts:
        amps = amps[:, -args.max_time_pkts :]
        ts = ts[-args.max_time_pkts :]
        T = amps.shape[1]

    # x-axis: convert absolute timestamps to relative seconds if they look
    # like epoch times, otherwise just use index
    if ts[0] > 1e9:  # looks like Unix timestamp
        t_rel = ts - ts[0]
        xlabel = "Time (s)"
    else:
        t_rel = ts
        xlabel = "Packet index"

    # normalization
    norm = None
    if args.normalize:
        norm = mcolors.LogNorm(
            vmin=args.vmin if args.vmin is not None else max(amps.min(), 1),
            vmax=args.vmax if args.vmax is not None else amps.max(),
        )

    fig, ax = plt.subplots(figsize=(12, 6))
    fig.suptitle(args.title, fontsize=13)

    im = ax.pcolormesh(
        np.arange(T + 1),
        np.arange(lo, hi + 2),  # subcarrier edges
        amps,
        cmap=args.colormap,
        norm=norm,
        shading="flat",
        vmin=args.vmin if args.vmin is not None and not args.normalize else None,
        vmax=args.vmax if args.vmax is not None and not args.normalize else None,
    )

    ax.set_xlabel(xlabel)
    ax.set_ylabel("Subcarrier index")
    ax.set_title(f"Subcarriers {lo}–{hi},  {T} packets")

    # set x tick labels to actual time values (sparse)
    n_ticks = min(10, T)
    tick_idx = np.linspace(0, T - 1, n_ticks, dtype=int)
    ax.set_xticks(tick_idx + 0.5)
    ax.set_xticklabels(
        [
            f"{t_rel[i]:.1f}"
            if args.input and str(args.input).endswith(".csv")
            else str(i)
            for i in tick_idx
        ],
        fontsize=8,
    )

    cbar = fig.colorbar(im, ax=ax, pad=0.02)
    cbar.set_label("Amplitude")

    ax.set_aspect(args.aspect)
    fig.tight_layout()

    if args.save:
        fig.savefig(args.save, dpi=args.dpi)
        print(f"Saved: {args.save}", file=sys.stderr)
    else:
        plt.show()


def build_time_edges(ts):
    """
    Given a strictly-increasing 1-D array of sample times of length T,
    return an array of length T+1 describing the cell boundaries
    (midpoints between samples, extrapolated at the two ends).
    Required because pcolormesh(shading='flat') wants edges, not centres.
    """
    if len(ts) < 2:
        return np.array([ts[0], ts[0] + 1.0])
    mid = (ts[1:] + ts[:-1]) / 2.0
    left = ts[0] - (ts[1] - ts[0]) / 2.0
    right = ts[-1] + (ts[-1] - ts[-2]) / 2.0
    return np.concatenate([[left], mid, [right]])


def live_heatmap(args):
    read_fn, close_fn, is_csv = build_reader(args)
    no_brackets = is_csv or args.no_brackets

    time_window = args.time_window
    amps_buf = []
    ts_buf = []
    total = 0
    K = None

    plt.ion()
    fig, ax = plt.subplots(figsize=(12, 6))
    fig.tight_layout()

    im = None
    cbar = None

    if args.subcarrier_range:
        sc_lo, sc_hi = (int(x) for x in args.subcarrier_range.split(":"))
    else:
        sc_lo, sc_hi = None, None

    try:
        while True:
            row = read_fn()
            if row is None:
                break

            if isinstance(row, str):
                if not row or "CSI_DATA" not in row:
                    continue
                amps = parse_line(row, args.csi_index)
                t = time.time()
            else:
                amps = parse_csv_row(row, args.csi_index, no_brackets)
                try:
                    t = float(row[args.ts_col])
                except (ValueError, IndexError):
                    t = time.time()

            if amps is None:
                continue

            if K is None:
                K = len(amps)
            elif len(amps) != K:
                continue

            amps_buf.append(np.asarray(amps, dtype=np.float64))
            ts_buf.append(t)
            total += 1

            # prune packets older than the time window
            while ts_buf and ts_buf[0] < t - time_window:
                amps_buf.pop(0)
                ts_buf.pop(0)

            if len(amps_buf) < 2:
                continue

            if total % max(1, int(1.0 / args.render_rate)) != 0:
                continue

            # ── build matrix: shape (K, T) ──
            matrix = np.column_stack(amps_buf)
            t_arr = np.asarray(ts_buf, dtype=np.float64)

            # ── slice subcarriers ──
            if sc_lo is not None:
                lo_c = max(0, min(sc_lo, K - 1))
                hi_c = max(0, min(sc_hi, K - 1))
                matrix = matrix[lo_c : hi_c + 1, :]
            else:
                lo_c, hi_c = 0, K - 1

            # x-extent: 0 at the oldest visible sample, x_max at newest
            x_min = t_arr[0] - t_arr[-1]  # negative
            x_max = 0.0
            y_min = lo_c
            y_max = hi_c + 1.0
            extent = (x_min, x_max, y_min, y_max)

            if im is None:
                im = ax.imshow(
                    matrix,
                    origin="lower",
                    aspect="auto",
                    extent=extent,
                    cmap=args.colormap,
                    vmin=args.vmin,
                    vmax=args.vmax,
                )
                ax.set_xlabel("Time (s, 0 = now)")
                ax.set_ylabel("Subcarrier index")
                cbar = fig.colorbar(im, ax=ax, pad=0.02)
                cbar.set_label("Amplitude")
            else:
                im.set_data(matrix)  # ← no ravel, no shape juggling
                im.set_extent(extent)

            ax.set_xlim(-time_window, 0)  # lock to the 15-second view
            ax.set_ylim(lo_c, hi_c + 1)
            ax.set_title(
                f"{args.title}  —  last {time_window:.0f} s, "
                f"sc {lo_c}–{hi_c},  {len(amps_buf)} pkts, total={total}"
            )
            fig.canvas.flush_events()
            plt.pause(0.01)

    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
    finally:
        close_fn()
        plt.close(fig)


def main():
    args = parse_args()

    if args.live or args.stdin:
        live_heatmap(args)
    else:
        amps, ts = collect_data(args)
        print(
            f"Collected {amps.shape[1]} packets × {amps.shape[0]} subcarriers",
            file=sys.stderr,
        )
        plot_heatmap(amps, ts, args)


if __name__ == "__main__":
    main()
