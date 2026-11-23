#!/usr/bin/env python3
"""Live, local-only CSI amplitude dashboard for ESP32-C5 serial output."""

import argparse
from collections import deque
import json
import math
from queue import Empty, Queue
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
CHANNELS = set(range(1, 12)) | {36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144, 149, 153, 157, 161, 165}


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
        self.channels = sorted(CHANNELS if sample_format == "c5-12bit" else set(range(1, 12)))
        self.status = "Connecting to board…"
        self.sources = {}
        self.total = 0
        self.history = deque(maxlen=2048)
        self.access_points = {}
        self.stations = {}
        self.channel = None
        self.channel_control = False
        self.channel_error = None
        self.connected = False
        self.commands = Queue()

    def observe(self, line):
        parts = line.split(",")
        with self.lock:
            if len(parts) == 2 and parts[0] == "CHANNEL":
                try:
                    reported = int(parts[1])
                except ValueError:
                    return
                self.channel_control = True
                self.channel_error = None
                if reported != self.channel:
                    self.channel = reported
                    self.sources.clear()
                    self.history.clear()
                    self.access_points.clear()
                    self.stations.clear()
                    self.status = f"Listening on channel {self.channel}"
            elif len(parts) == 3 and parts[0] == "AP_INFO":
                self.channel_control = True
                try:
                    ssid = bytes.fromhex(parts[2]).decode("utf-8", errors="replace")
                except ValueError:
                    return
                self.access_points[parts[1].upper()] = "".join(c if c.isprintable() else "�" for c in ssid)
            elif len(parts) == 3 and parts[0] == "STA_INFO":
                self.stations[parts[1].upper()] = parts[2].upper()
            elif parts[0] == "CHANNEL_ERROR":
                self.channel_error = parts[1] if len(parts) > 1 else "unknown"

    def request_channel(self, channel):
        with self.lock:
            if not self.connected or not self.channel_control:
                return False
            self.channel_error = None
            self.status = f"Switching to channel {channel}…"
        self.commands.put_nowait(channel)
        return True

    def update(self, sample):
        now = time.monotonic()
        with self.lock:
            if self.channel is None:
                self.channel = sample["channel"]
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
                "channel": self.channel,
                "channel_control": self.channel_control,
                "channel_error": self.channel_error,
                "channels": self.channels,
                "format": self.sample_format,
                "total": self.total,
                "packets": [packet for packet in self.history if packet["seq"] > since],
                "sources": [
                    {key: value for key, value in sample.items() if key not in ("rate_at", "rate_count", "seen_at")}
                    | {"age": round(now - sample["seen_at"], 1),
                       "bssid": self.stations.get(mac),
                       "ssid": self.access_points.get(self.stations.get(mac, mac)),
                       "identity_role": "AP" if mac in self.access_points else "station" if mac in self.stations else "unknown"}
                    for mac, sample in self.sources.items()
                    if now - sample["seen_at"] < 15
                ],
            }


def read_serial(port, baud, state):
    while True:
        try:
            with serial.Serial(port, baudrate=baud, timeout=0.2) as connection:
                with state.lock:
                    state.connected = True
                    state.status = f"Connected to {port}; waiting for CSI…"
                while True:
                    try:
                        channel = state.commands.get_nowait()
                    except Empty:
                        pass
                    else:
                        connection.write(f"CH {channel}\n".encode("ascii"))
                    line = connection.readline().decode("utf-8", errors="replace").strip()
                    if line.startswith(("CHANNEL,", "CHANNEL_ERROR,", "AP_INFO,", "STA_INFO,")):
                        state.observe(line)
                    sample = parse_csi_line(line, state.sample_format)
                    if sample:
                        state.update(sample)
        except (OSError, serial.SerialException) as error:
            with state.lock:
                state.connected = False
                state.status = f"Serial unavailable: {error}"
            time.sleep(2)


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if urlsplit(self.path).path != "/api/channel":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 128:
                    raise ValueError
                channel = json.loads(self.rfile.read(length))["channel"]
                if type(channel) is not int or channel not in state.channels:
                    raise ValueError
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                self.send_error(400, "invalid channel")
                return
            if not state.request_channel(channel):
                self.send_error(503, "board serial port unavailable")
                return
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()

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
