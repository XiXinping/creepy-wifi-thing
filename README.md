# Live Wi-Fi CSI on ESP32-C5 and ESP32-C6

The firmware in [`creepy-wifi-thing/`](./creepy-wifi-thing/) uses `esp-csi-rs` 0.13 to capture Wi-Fi channel state information. It defaults to the Seeed Studio XIAO ESP32-C5 and also builds for ESP32-C6 with a Cargo feature. The `sniffer` firmware listens on a chosen channel and prints CSI records in ESP32-CSI-Tool format over USB. The `follower` firmware joins a hotspot or router and measures that link. Both boards use the same source files.

The vendored [`ESP32-CSI-Tool/`](./ESP32-CSI-Tool/) is the original ESP-IDF 4.3 project. That release predates the ESP32-C5 and is kept as a reference; use the Rust firmware for this board.

## Build and flash

1. Install [Rust and rustup](https://rustup.rs/) and [espflash](https://github.com/esp-rs/espflash). The project requests nightly Rust, `rust-src`, and the `riscv32imac-unknown-none-elf` target in its toolchain file. Load Cargo into the shell with `. "$HOME/.cargo/env"` if `cargo` is not found.
2. Connect the XIAO ESP32-C5 through its USB-C data port. If automatic reset fails, hold **BOOT**, tap **RESET**, then release **BOOT** to enter download mode.
3. In [`creepy-wifi-thing/src/bin/sniffer.rs`](./creepy-wifi-thing/src/bin/sniffer.rs), set `CHANNEL` to the primary channel of the Wi-Fi traffic you want to measure. Channels 1–11 are the simplest starting point; the ESP32-C5 can also use supported 5 GHz channels. The ESP32-C6 uses 2.4 GHz only.
4. From the Rust project directory, build and flash:

   ```sh
   cd creepy-wifi-thing
   cargo build --release --bin sniffer
   cargo run --release --bin sniffer
   ```

`cargo run` uses `espflash` to flash the board and monitor its output. If several serial ports are connected, set `ESPFLASH_PORT` to the XIAO's port before running it. Press Ctrl+C to stop monitoring.

The monitor should show `Starting sniffer ...` and then `CSI_DATA` lines when frames arrive on the selected channel. A once-per-second line reports CSI packets per transmitter. If it says `No CSI yet`, confirm the channel and generate traffic on that Wi-Fi network. A phone or laptop connected to the network can provide traffic; no second ESP32 is required.

`HE20` in the sniffer source enables HE-LTF capture for suitable Wi-Fi 6 frames. Leave it `false` for an initial check. The `follower` binary reads its SSID and password from build-time environment variables.

The XIAO uses the ESP32-C5's native USB Serial/JTAG connection for logging. The board has no built-in SD card, so capture records on the host by saving the monitor output.

## Live amplitude GUI

The local dashboard has a **Trend** view with the latest CSI spectrum and a 60-second amplitude trace for a selected transmitter and CSI sample. The **Heatmap** view shows every decoded sample over the same 60-second window, with packet time from left to right and sample index from top to bottom. It decodes the ESP32-C5's signed 12-bit I/Q samples; the older `ESP32-CSI-Tool` plotter assumes 8-bit samples and is unsuitable for this capture format.

With either firmware flashed, stop any other serial monitor and run:

```sh
cd creepy-wifi-thing
python3 -m venv .venv
.venv/bin/python -m pip install -r tools/requirements.txt
.venv/bin/python tools/live_csi_gui.py --port /dev/cu.usbmodem101
```

Open <http://127.0.0.1:8765/>. Change `--port` if the board appears under another serial device name. The server binds only to `127.0.0.1`. Press Ctrl+C in its terminal to stop it. For ESP32-C6 or older 8-bit CSI firmware, pass `--sample-format 8bit`.

The slider's maximum follows the latest packet's decoded sample count. A 104-byte ESP32-C5 capture contains 26 complex I/Q values, indexed **0–25**. A different frame or capture format can have a different count. Sample 0 can be invalid on ESP32-C5 hardware, so start with sample 1 when comparing amplitude over time.

The heatmap outlines up to five **potential motion channels**. For each sample, the GUI divides amplitude by the packet's median amplitude to reduce whole-packet gain changes. It compares the last 5 seconds with a baseline from 10–60 seconds ago, using median and median absolute deviation. A score of 3 or more highlights a recent shift or rise in variation. At least 12 baseline packets and 5 recent packets are required. Click a candidate to open its trend. These are CSI change candidates, not a verified motion detector: traffic, interference, a moving phone or other radio changes can also produce scores. For a useful baseline, keep the phone and board still during the first 10–20 seconds, then move in the space between them.

## Measure the iPhone hotspot ↔ XIAO link

The flashed `sniffer` does not join any Wi-Fi network: it captures frames from every transmitter on its fixed `CHANNEL`. To measure packets exchanged specifically between your iPhone hotspot and the XIAO, use the `follower` station firmware. Its SSID is your iPhone's [Personal Hotspot name](https://support.apple.com/en-mide/guide/iphone/iph45447ca6/27/ios/27) (the iPhone device name), and its password is the Wi-Fi Password shown in **Settings → Personal Hotspot**. Turn on **Allow Others to Join**. For a reliable first connection, turn on **Maximize Compatibility** on an iPhone 12 or later; [Apple says this uses 2.4 GHz and WPA2](https://support.apple.com/guide/security/wi-fi-security-with-apple-devices-secfd166f620/web).

The station scans for the hotspot, so leave `CHANNEL_HINT` as `None`; you do not need to choose or enter a channel. The iPhone selects its hotspot channel. The station sends pings to the hotspot and reports CSI from received frames. Build and flash with credentials supplied at build time, keeping them out of the repository:

```sh
cd creepy-wifi-thing
CSI_WIFI_SSID='Your Wi-Fi SSID' CSI_WIFI_PASSWORD='Your hotspot password' cargo run --release --bin follower
```

Stop the flash monitor, then run the live GUI as above. The GUI will plot CSI from the associated hotspot link. The channel can change if the hotspot restarts, so leave `CHANNEL_HINT` unset for `follower`. The `follower` firmware must be rebuilt when the hotspot name or password changes. Do not commit the password to the repository.

If the monitor repeatedly reports `NoAccessPointFound`, check that the hotspot is on and advertising the exact SSID, including spelling and capitalization. On an iPhone, keep the Personal Hotspot settings screen open during initial discovery and try **Maximize Compatibility**. A successful join prints `WiFi Connected`; until then, CSI seen while scanning other networks is not a measurement of the intended hotspot link.

## ESP32-C6 build

The same `sniffer` and `follower` sources support ESP32-C6. Disable the C5 default feature and enable C6; use a separate target directory to avoid mixing chip-specific build outputs:

```sh
cd creepy-wifi-thing
CARGO_TARGET_DIR=target/esp32c6 cargo build --release --no-default-features --features esp32c6 --bin sniffer
CARGO_TARGET_DIR=target/esp32c6 espflash flash --chip esp32c6 --monitor target/esp32c6/riscv32imac-unknown-none-elf/release/sniffer
```

For C6 `follower`, add `CSI_WIFI_SSID` and `CSI_WIFI_PASSWORD` to the build command and replace `--bin sniffer` and the flashed binary path with `follower`. Run the GUI with `--sample-format 8bit`. The C6 binaries were cross-compiled; only the XIAO ESP32-C5 was flashed and tested on hardware.
