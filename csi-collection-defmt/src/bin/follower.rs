//! **ESP-NOW simplex** — the asymmetric exchange, and the highest CSI rate the crate offers.
//!
//! | Attribute | `END = Source` | `END = Peer` |
//! |---|---|---|
//! | Mode | ESP-NOW simplex | ESP-NOW simplex |
//! | Session role | **Initiator** — it originates the flood | **Responder** |
//! | Sensing role | **Transmitter** — it owns all transmit airtime | **Receiver** |
//! | Reporting | **None** — it measures nothing | **Report** (to the host) |
//!
//! None of these is settable: the asymmetry fixes them all. The source floods, the peer measures.
//! Leaving the channel to a single transmitter is exactly why this sustains a markedly higher
//! sample rate than the symmetric `esp_now` pairing.
//!
//! How the link forms: the **peer** broadcasts a sparse (~1 Hz) discovery beacon until it hears a
//! source; the source learns its MAC, registers it as a unicast peer with a forced PHY, sends one
//! hello so the peer stops beaconing, and then unicasts continuously. After that the peer is purely
//! receive-only.
//!
//! Logging on this build: defmt frames over RTT, decoded by probe-rs
//! (`probe-rs run --chip=ESP32C6`). The global logger is provided by `rtt-target`
//! (esp-csi-rs is built with `external-defmt-logger`), so `rtt_init_defmt!` must run
//! before anything logs — anything emitted earlier is silently dropped.
#![no_std]
#![no_main]

use embassy_executor::Spawner;
use embassy_time::{Duration, Timer};
use esp_backtrace as _; // esp-backtrace panic-handler (defmt variant); no esp_println here
use esp_csi_rs::csi::CsiPacket;
use esp_csi_rs::logging::logging::{LogMode, init_logger};
use esp_csi_rs::{
    CsiConfig, CsiNode, CsiNodeClient, EspNowConfig, MeasurementSetup, NodeHardware,
    install_static_espnow_recv, log_ln, set_csi_callback,
};
use esp_hal::clock::CpuClock;
use esp_hal::timer::timg::TimerGroup;
use esp_radio::esp_now::WifiPhyRate;
use esp_radio::wifi::WifiController;
use portable_atomic::{AtomicI32, AtomicU32, Ordering};

extern crate alloc;

// ── Knobs ───────────────────────────────────────────────────────────────────────────────────────
#[allow(dead_code)]
#[derive(PartialEq, Eq)]
enum End {
    /// Floods: initiator, sensing transmitter, reports nothing.
    Source,
    /// Measures: responder, sensing receiver, reports everything.
    Peer,
}

/// Flash the same binary to both boards, changing only this.
const END: End = End::Peer;
/// Both ends must agree.
const CHANNEL: u8 = 6;
/// Forced TX PHY for the flood. Source only — the peer has no rate to force.
const PHY_RATE: WifiPhyRate = WifiPhyRate::RateMcs7Lgi;
// ────────────────────────────────────────────────────────────────────────────────────────────────

static WIFI_CONTROLLER: static_cell::StaticCell<WifiController<'static>> =
    static_cell::StaticCell::new();

esp_bootloader_esp_idf::esp_app_desc!();

static LATEST_RSSI: AtomicI32 = AtomicI32::new(0);
static CSI_PKT_COUNT: AtomicU32 = AtomicU32::new(0);

fn on_csi(packet: &CsiPacket) {
    LATEST_RSSI.store(packet.rssi() as i32, Ordering::Relaxed);
    CSI_PKT_COUNT.fetch_add(1, Ordering::Relaxed);
}

#[embassy_executor::task]
async fn stats_task() {
    let mut previous = 0u32;
    loop {
        Timer::after_secs(1).await;
        let total = CSI_PKT_COUNT.load(Ordering::Relaxed);
        log_ln!(
            "CSI {}/s, total {}, RSSI {}",
            total.wrapping_sub(previous),
            total,
            LATEST_RSSI.load(Ordering::Relaxed),
        );
        previous = total;
    }
}

#[esp_rtos::main]
async fn main(spawner: Spawner) -> ! {
    let config = esp_hal::Config::default().with_cpu_clock(CpuClock::max());
    let peripherals = esp_hal::init(config);

    // RTT must come up before the first log line. Default is NoBlockSkip/1 KiB —
    // far too small for Text-mode CSI — so size it up and skip (never block) on
    // overrun: dropped frames show up as gaps, the WiFi hot path never stalls.
    rtt_target::rtt_init_defmt!(rtt_target::ChannelMode::NoBlockSkip, 32768);

    // Sets the CSI *format* (Text) and wires the crate's emission path. With
    // external-defmt-logger and no auto/async-print, the sync inline path is used
    // and every line goes out as a defmt frame over RTT.
    init_logger(spawner, LogMode::EspCsiTool);

    esp_alloc::heap_allocator!(#[esp_hal::ram(reclaimed)] size: 61440);
    let timg0 = TimerGroup::new(peripherals.TIMG0);
    esp_rtos::start(timg0.timer0, peripherals.FROM_CPU_INTR0);

    let config_radio = esp_radio::wifi::ControllerConfig::default();
    let wifi_controller = esp_radio::wifi::WifiController::new(peripherals.WIFI, config_radio)
        .expect("Failed to initialize Wi-Fi controller");
    install_static_espnow_recv();
    let controller = WIFI_CONTROLLER.init(wifi_controller);

    let mut node_handle = CsiNodeClient::new();
    let csi_hardware = NodeHardware::new(controller);

    let mut node = match END {
        End::Source => {
            log_ln!(
                "Starting simplex source (initiator, transmitter) on channel {}",
                CHANNEL
            );
            // No periodicity: the source floods as fast as the radio sends.
            CsiNode::esp_now_simplex_source(
                EspNowConfig::default()
                    .with_channel(CHANNEL)
                    .with_phy_rate(PHY_RATE),
                MeasurementSetup::new(),
                csi_hardware,
            )
        }
        End::Peer => {
            log_ln!(
                "Starting simplex peer (responder, receiver) on channel {}",
                CHANNEL
            );
            CsiNode::esp_now_simplex_peer(
                CHANNEL,
                MeasurementSetup::new().acquisition(CsiConfig::default()),
                csi_hardware,
            )
        }
    };
    node.set_protocol(esp_radio::wifi::Protocol::N);

    if END == End::Peer {
        set_csi_callback(on_csi);
        spawner.spawn(stats_task().unwrap());
    }

    let _ = &mut node_handle;
    node.run().await;

    loop {
        log_ln!("Simplex node stopped");
        Timer::after(Duration::from_secs(5)).await;
    }
}
