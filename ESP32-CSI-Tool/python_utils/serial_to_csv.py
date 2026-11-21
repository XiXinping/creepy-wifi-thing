#!/usr/bin/env python3
"""
csi_to_csv.py – Read CSI data from a USB serial device, timestamp each
packet, and write it to a CSV file.

Each CSI_DATA line gets a trailing timestamp column appended (same pattern
as the reference `serial_append_timestamp.py` utility):

    CSI_DATA,v1,v2,v3,...  →  v1,v2,v3,...,1718900123.456
"""

import sys
import argparse
import csv
import serial
import time


# ── CLI ──────────────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(
        description="Read CSI data from USB serial, timestamp it, write CSV."
    )
    parser.add_argument(
        "serial_port",
        help="USB serial port (e.g. /dev/ttyUSB0 or COM3)",
    )
    parser.add_argument(
        "output_csv",
        help="Output CSV file name",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="Baud rate (default: 115200)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=1.0,
        help="Serial read timeout in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--print-every",
        type=int,
        default=100,
        help="Print packet count every N packets (default: 100). 0 = off.",
    )
    parser.add_argument(
        "--no-timestamp",
        action="store_true",
        default=False,
        help="Do NOT append a Unix timestamp to each CSI line",
    )
    parser.add_argument(
        "--header",
        action="store_true",
        default=False,
        help="Write a header row (fields...,timestamp) at the top of the CSV",
    )
    return parser.parse_args()


# ── Serial helpers ───────────────────────────────────────────────────────────


def open_serial(port, baud, timeout):
    try:
        ser = serial.Serial(port, baudrate=baud, timeout=timeout)
        return ser
    except serial.SerialException as e:
        print(f"Error opening serial port '{port}': {e}", file=sys.stderr)
        sys.exit(1)


def readline(ser):
    """
    Read one line from the serial port, ignoring transient errors.
    """
    while True:
        try:
            raw = ser.readline()
            if raw:
                return raw.decode("utf-8", errors="replace").strip()
            return ""
        except serial.SerialException:
            pass
        except Exception:
            pass


def process_until_first_csi_line(ser, should_print):
    """
    Consume flash / debug lines until the first CSI_DATA line arrives.
    Returns that first CSI_DATA line.
    """
    if should_print:
        print("Printing Flash information", file=sys.stderr)
    while True:
        line = readline(ser)
        if "CSI_DATA" not in line:
            if line != "" and should_print:
                print(line, file=sys.stderr)
        else:
            break
    return line


def ignore_until_first_csi_line(ser):
    return process_until_first_csi_line(ser, should_print=False)


def print_until_first_csi_line(ser):
    return process_until_first_csi_line(ser, should_print=True)


# ── CSV writing ──────────────────────────────────────────────────────────────


def write_csi_to_csv(ser, first_csi_line, csv_path, args):
    """
    Write the first (and all subsequent) CSI_DATA lines to `csv_path`.
    Each line is parsed after the "CSI_DATA" tag, comma-split, and
    optionally appended with a Unix timestamp.
    """
    print(f"Writing CSI data to '{csv_path}' … (Ctrl-C to stop)", file=sys.stderr)

    packet_count = 0
    start_time = time.time()
    add_ts = not args.no_timestamp

    with open(csv_path, mode="w", newline="") as f:
        writer = csv.writer(f)

        # ── optional header row ──
        if args.header:
            # We don't know the field count yet; use a generic label.
            # The caller can rename later, or set --header off.
            # We'll fill it in after the first line is parsed.
            pass  # handled below after first parse

        # ── first CSI line (already captured) ──
        fields = _parse_csi(first_csi_line)
        if fields is None:
            print("First CSI line could not be parsed.", file=sys.stderr)
            return 0

        if args.header:
            header = [f"f{i}" for i in range(len(fields))]
            if add_ts:
                header.append("timestamp")
            writer.writerow(header)

        _write_row(writer, fields, add_ts)
        packet_count += 1

        if args.print_every:
            print(f"  Packets received: {packet_count}", file=sys.stderr)

        # ── streaming loop ──
        while True:
            line = readline(ser)
            if line == "":
                continue
            if "CSI_DATA" not in line:
                continue

            fields = _parse_csi(line)
            if fields is None:
                continue

            _write_row(writer, fields, add_ts)
            packet_count += 1

            if args.print_every and packet_count % args.print_every == 0:
                elapsed = time.time() - start_time
                rate = packet_count / elapsed if elapsed > 0 else 0
                print(
                    f"  Packets received: {packet_count}  "
                    f"({rate:.1f} pkt/s, elapsed {elapsed:.1f}s)",
                    file=sys.stderr,
                )

    return packet_count


# ── tiny helpers ─────────────────────────────────────────────────────────────


def _parse_csi(line):
    """
    Strip the leading "CSI_DATA" tag and split on commas.
    Returns a list of raw value strings, or None on failure.
    """
    try:
        _, payload = line.split("CSI_DATA", 1)
        return payload.strip().split(",")
    except ValueError:
        return None


def _write_row(writer, fields, add_timestamp):
    """
    Write one row; optionally append the current Unix timestamp.
    """
    if add_timestamp:
        fields = fields + [str(time.time())]
    writer.writerow(fields)


# ── main ─────────────────────────────────────────────────────────────────────


def main():
    args = parse_args()

    ser = open_serial(args.serial_port, args.baud, args.timeout)

    try:
        first_csi_line = ignore_until_first_csi_line(ser)
        # first_csi_line = print_until_first_csi_line(ser)

        total = write_csi_to_csv(ser, first_csi_line, args.output_csv, args)

    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        total = 0
    finally:
        ser.close()
        print("Serial port closed.", file=sys.stderr)

    print(f"Total CSI packets written to '{args.output_csv}': {total}", file=sys.stderr)


if __name__ == "__main__":
    main()
