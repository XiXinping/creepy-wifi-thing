#!/usr/bin/env python3
"""Live, local-only CSI amplitude dashboard for ESP32-C5 serial output."""

import argparse
from collections import deque
import json
import math
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import serial


INDEX_HTML = Path(__file__).with_name("live_csi_gui.html")
NUMBERS = re.compile(r"-?\d+")


def decode_c5_12bit(raw):
    """Decode little-endian signed 12-bit I/Q words in the C5 CSI byte stream."""
    amplitudes = []
    for offset in range(0, len(raw) - 3, 4):
        words = []
        for index in (offset, offset + 2):
            value = ((raw[index] & 255) | ((raw[index + 1] & 255) << 8)) & 0xFFF
            words.append(value - 0x1000 if value & 0x800 else value)
        amplitudes.append(round(math.hypot(*words), 2))
    return amplitudes


def decode_8bit(raw):
    return [round(math.hypot(raw[i], raw[i + 1]), 2) for i in range(0, len(raw) - 1, 2)]


def parse_csi_line(line, sample_format="c5-12bit"):
    if not line.startswith("CSI_DATA,"):
        return None
    fields = line.split(",", 25)
    if len(fields) != 26 or not fields[25].startswith("["):
        return None
    try:
        raw = [int(value) for value in NUMBERS.findall(fields[25])]
        expected = int(fields[24])
        if expected != len(raw) or not raw or any(value < -128 or value > 127 for value in raw):
            return None
        amplitudes = decode_c5_12bit(raw) if sample_format == "c5-12bit" else decode_8bit(raw)
        if not amplitudes:
            return None
        return {
            "mac": fields[2],
            "role": fields[1],
            "rssi": int(fields[3]),
            "channel": int(fields[16]),
            "amplitudes": amplitudes,
        }
    except ValueError:
        return None


class CsiState:
    def __init__(self, sample_format):
        self.lock = threading.Lock()
        self.sample_format = sample_format
        self.status = "Connecting to board…"
        self.sources = {}
        self.total = 0
        self.history = deque(maxlen=2048)

    def update(self, sample):
        now = time.monotonic()
        with self.lock:
            current = self.sources.get(sample["mac"])
            count = current["count"] + 1 if current else 1
            if current and now - current["rate_at"] < 1:
                rate, rate_at, rate_count = current["rate"], current["rate_at"], current["rate_count"]
            else:
                previous_count = current["rate_count"] if current else 0
                elapsed = max(now - current["rate_at"], 0.001) if current else 1
                rate = round((count - previous_count) / elapsed, 1)
                rate_at, rate_count = now, count
            self.sources[sample["mac"]] = {
                **sample,
                "count": count,
                "rate": rate,
                "rate_at": rate_at,
                "rate_count": rate_count,
                "seen_at": now,
            }
            self.total += 1
            self.history.append({
                "seq": self.total,
                "time": round(time.time() * 1000),
                "mac": sample["mac"],
                "amplitudes": sample["amplitudes"],
            })
            self.status = "Receiving CSI"

    def snapshot(self, since=0):
        now = time.monotonic()
        cutoff = round(time.time() * 1000) - 60000
        with self.lock:
            while self.history and self.history[0]["time"] < cutoff:
                self.history.popleft()
            return {
                "status": self.status,
                "format": self.sample_format,
                "total": self.total,
                "packets": [packet for packet in self.history if packet["seq"] > since],
                "sources": [
                    {key: value for key, value in sample.items() if key not in ("rate_at", "rate_count", "seen_at")}
                    | {"age": round(now - sample["seen_at"], 1)}
                    for sample in self.sources.values()
                    if now - sample["seen_at"] < 15
                ],
            }


def read_serial(port, baud, state):
    while True:
        try:
            with serial.Serial(port, baudrate=baud, timeout=0.2) as connection:
                with state.lock:
                    state.status = f"Connected to {port}; waiting for CSI…"
                while True:
                    line = connection.readline().decode("utf-8", errors="replace").strip()
                    sample = parse_csi_line(line, state.sample_format)
                    if sample:
                        state.update(sample)
        except (OSError, serial.SerialException) as error:
            with state.lock:
                state.status = f"Serial unavailable: {error}"
            time.sleep(2)


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == "/":
                body = INDEX_HTML.read_bytes()
                content_type = "text/html; charset=utf-8"
            elif url.path == "/api/state":
                try:
                    since = max(0, int(parse_qs(url.query).get("since", ["0"])[0]))
                except ValueError:
                    self.send_error(400, "since must be an integer")
                    return
                body = json.dumps(state.snapshot(since)).encode("utf-8")
                content_type = "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/cu.usbmodem101", help="Board serial port")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--http-port", type=int, default=8765)
    parser.add_argument("--sample-format", choices=("c5-12bit", "8bit"), default="c5-12bit")
    args = parser.parse_args()
    state = CsiState(args.sample_format)
    thread = threading.Thread(target=read_serial, args=(args.port, args.baud, state), daemon=True)
    thread.start()
    server = ThreadingHTTPServer(("127.0.0.1", args.http_port), make_handler(state))
    print(f"Live CSI GUI: http://127.0.0.1:{server.server_port}", file=sys.stderr, flush=True)
    print(f"Reading {args.port} at {args.baud} baud", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
