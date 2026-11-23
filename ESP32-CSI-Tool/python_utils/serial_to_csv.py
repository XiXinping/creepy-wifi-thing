#!/usr/bin/env python3
"""
serial_to_csv.py – Read CSI_DATA from a USB serial port, timestamp each
packet, and write it to a CSV file.

Two scheduling options:

    --start-delay SEC   Sleep before opening the port. Lets the device
                        power up / associate / emit flash output so that
                        the first CSI_DATA line you capture is "clean".
                        Default 0 (no delay).

    --duration SEC      Stop collecting after this many seconds. The
                        timer starts on the first CSI_DATA packet you
                        write, not at program launch. Pass 0 to run
                        indefinitely. Default 30.
"""

import sys
import argparse
import csv
import time


# ── CLI ──────────────────────────────────────────────────────────────────────


def parse_args():
    p = argparse.ArgumentParser(
        description="Capture CSI_DATA from a serial port into a CSV."
    )
    p.add_argument("serial_port", help="e.g. /dev/ttyUSB0 or COM3")
    p.add_argument("output_csv", help="Output CSV file name")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument(
        "--timeout", type=float, default=1.0, help="Serial read timeout in seconds"
    )
    p.add_argument(
        "--start-delay",
        type=float,
        default=0.0,
        metavar="SEC",
        help="Sleep this long after opening the port, then flush "
        "the input buffer, before starting capture "
        "(default 0)",
    )
    p.add_argument(
        "--duration",
        type=float,
        default=30.0,
        metavar="SEC",
        help="Stop after this many seconds of active capture. "
        "0 = run forever (default 30)",
    )
    p.add_argument(
        "--print-every",
        type=int,
        default=100,
        help="Print a progress line every N packets (0 = off)",
    )
    p.add_argument(
        "--no-timestamp",
        action="store_true",
        help="Do not append a Unix timestamp column",
    )
    p.add_argument(
        "--header", action="store_true", help="Write a header row at the top of the CSV"
    )
    return p.parse_args()


# ── Serial helpers ───────────────────────────────────────────────────────────


def open_serial(port, baud, timeout):
    try:
        import serial
    except ImportError:
        raise SystemExit("pyserial not installed (pip install pyserial)")
    try:
        return serial.Serial(port, baudrate=baud, timeout=timeout)
    except serial.SerialException as e:
        raise SystemExit(f"Error opening '{port}': {e}")


def read_line(ser):
    """Read one line from the serial port, ignoring transient errors."""
    while True:
        try:
            raw = ser.readline()
            if raw:
                return raw.decode("utf-8", errors="replace").strip()
            return ""
        except Exception:
            pass


def skip_until_first_csi(ser):
    """Consume flash/debug output until the first CSI_DATA line arrives."""
    print("Waiting for first CSI_DATA line …", file=sys.stderr)
    while True:
        line = read_line(ser)
        if "CSI_DATA" in line:
            return line


# ── CSV writing ──────────────────────────────────────────────────────────────


def parse_csi_payload(line):
    """Strip the CSI_DATA tag and split on commas."""
    try:
        _, payload = line.split("CSI_DATA", 1)
        return [f.strip() for f in payload.strip().split(",")]
    except ValueError:
        return None


def write_row(writer, fields, add_timestamp):
    if add_timestamp:
        fields = fields + [f"{time.time():.3f}"]
    writer.writerow(fields)


def capture_loop(ser, first_line, csv_path, args):
    """
    Write every CSI_DATA line (starting with first_line) to csv_path.
    Stops when --duration expires or the serial stream ends.
    """
    add_ts = not args.no_timestamp
    started = time.monotonic()
    deadline = None if args.duration <= 0 else started + args.duration
    total = 0
    pkt_per_period = 0

    with open(csv_path, mode="w", newline="") as f:
        w = csv.writer(f)

        # first payload
        fields = parse_csi_payload(first_line)
        if fields is None:
            print("First CSI line unparseable.", file=sys.stderr)
            return 0

        if args.header:
            head = [f"c{i}" for i in range(len(fields))]
            if add_ts:
                head.append("timestamp")
            w.writerow(head)

        write_row(w, fields, add_ts)
        total += 1
        pkt_per_period += 1

        while True:
            line = read_line(ser)
            if line == "":
                # serial timeout — check whether we've hit the deadline
                if deadline is not None and time.monotonic() >= deadline:
                    break
                continue
            if "CSI_DATA" not in line:
                continue

            fields = parse_csi_payload(line)
            if fields is None:
                continue

            write_row(w, fields, add_ts)
            total += 1
            pkt_per_period += 1

            if args.print_every and total % args.print_every == 0:
                elapsed = time.monotonic() - started
                remaining = (deadline - time.monotonic()) if deadline else -1
                rate = total / elapsed if elapsed > 0 else 0
                rem_str = f", {remaining:5.1f}s left" if deadline else ""
                print(
                    f"  {total:>6d} pkts  ({rate:5.1f}/s, "
                    f"elapsed {elapsed:5.1f}s{rem_str})",
                    file=sys.stderr,
                )
                pkt_per_period = 0

            if deadline is not None and time.monotonic() >= deadline:
                break

    return total


# ── main ─────────────────────────────────────────────────────────────────────


def main():
    args = parse_args()

    if args.start_delay > 0:
        print(
            f"Sleeping {args.start_delay:.1f}s (device boot / settle) …",
            file=sys.stderr,
        )
        time.sleep(args.start_delay)

    ser = open_serial(args.serial_port, args.baud, args.timeout)

    if args.start_delay > 0:
        # discard anything that piled up during the sleep
        try:
            ser.reset_input_buffer()
        except Exception:
            pass

    try:
        first = skip_until_first_csi(ser)

        if args.duration > 0:
            print(
                f"Capturing for {args.duration:.1f}s (Ctrl-C to stop early) …",
                file=sys.stderr,
            )
        else:
            print("Capturing until Ctrl-C …", file=sys.stderr)

        try:
            total = capture_loop(ser, first, args.output_csv, args)
        except KeyboardInterrupt:
            print("\nInterrupted.", file=sys.stderr)
            total = 0  # overwritten below by file count

    finally:
        ser.close()

    # count what actually landed on disk (covers the Ctrl-C case)
    with open(args.output_csv) as f:
        n = sum(1 for _ in f) - (1 if args.header else 0)

    print(f"Done. Wrote {n} packets to '{args.output_csv}'.", file=sys.stderr)


if __name__ == "__main__":
    main()
