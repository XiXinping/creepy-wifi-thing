#![no_std]
#![no_main]

use defmt::println;
use defmt_rtt as _;
use embassy_executor::Spawner;
use embassy_futures::join::join;
use embassy_sync::blocking_mutex::Mutex;
use embassy_sync::blocking_mutex::raw::CriticalSectionRawMutex;
use embassy_time::{Duration, Timer};
use esp_csi_rs::csi::CsiPacket;
use esp_csi_rs::logging::logging::{LogMode, init_logger, log_csi};
use esp_csi_rs::wire::{CsiPayload, PpduFormat};
use esp_csi_rs::{
    CsiConfig, CsiNode, CsiNodeClient, MeasurementSetup, NodeHardware, Reporting, SessionRole,
    Threshold, WifiApConfig, WifiStationConfig, set_csi_callback,
};
use esp_hal::clock::CpuClock;
use esp_hal::timer::timg::TimerGroup;
use esp_radio::wifi::ap::AccessPointConfig;
use esp_radio::wifi::sta::StationConfig;
use esp_radio::wifi::{AuthenticationMethodConfig, Password, WifiController};
use {esp_backtrace as _, esp_println as _};

extern crate alloc;

/// Channel to lock. Must match the emitter's primary channel when pairing with one.
const CHANNEL: u8 = 11;

/// Must match `access_point` (or your own AP).
const SSID: &str = "creepywifithing";
const PASSWORD: &str = "birdsarenotreal";

/// `Some(Initiator)` if this node's uplink is the traffic being measured; `None` if it only
/// measures a link someone else keeps busy.
const SESSION_ROLE: Option<SessionRole> = Some(SessionRole::Initiator);

/// DHCP lease pool size. Each client gets a distinct address (MAC to IP binding).
const LEASE_POOL: u8 = 1;

/// Fire one unicast frame to *every* active lease per tick instead of one lease per tick.
const SYNC_BURST: bool = false;

/// Gateway ping rate (Hz): the measurement periodicity, the uplink a reporting AP measures. Set
/// `None` for a passive station that generates no traffic of its own.
const PING_RATE_HZ: Option<u16> = Some(4000);

/// `Report { to }`, `ThresholdBased(..)` or `Decimate(n)`. A threshold must be calibrated: run
/// with `Threshold::new(level, hold_ms).variation_only()` first and read the scores a still room
/// gives.
const REPORTING: Reporting = Reporting::HOST;

#[allow(dead_code)]
const EXAMPLE_THRESHOLD: Threshold = Threshold::new(6000, 500);

/// HE-LTF capture (ESP32-C5 / C6 only).
const HE20: bool = false;

/// How many distinct transmitters to track.
const MAX_SOURCES: usize = 4;

static WIFI_CONTROLLER: static_cell::StaticCell<WifiController<'static>> =
    static_cell::StaticCell::new();

esp_bootloader_esp_idf::esp_app_desc!();

#[derive(Clone, Copy)]
struct SourceTally {
    mac: [u8; 6],
    count: u32,
    rssi: i32,
    ppdu: PpduFormat,
    /// Subcarriers the buffer's layout maps; 0 when the layout is unknown.
    mapped: usize,
}

fn ppdu_name(p: PpduFormat) -> &'static str {
    match p {
        PpduFormat::Dsss => "DSSS",
        PpduFormat::NonHt => "non-HT",
        PpduFormat::Ht => "HT",
        PpduFormat::Vht | PpduFormat::VhtMu => "VHT",
        PpduFormat::HeSu => "HE SU",
        PpduFormat::HeMu | PpduFormat::HeErSu | PpduFormat::HeTb => "HE",
        _ => "?",
    }
}

/// Per-transmitter tallies, written from the CSI callback and drained by the reporting task.
static SOURCES: Mutex<
    CriticalSectionRawMutex,
    core::cell::RefCell<heapless::Vec<SourceTally, MAX_SOURCES>>,
> = Mutex::new(core::cell::RefCell::new(heapless::Vec::new()));

fn on_csi(packet: &CsiPacket) {
    log_csi(packet.clone());
    // Two `i8` samples per subcarrier, so this is the width of the capture.
    SUBCARRIERS.store(
        packet.subcarriers() as u32,
        core::sync::atomic::Ordering::Relaxed,
    );
    let ppdu = packet.meta().ppdu;
    let mapped = match &packet.frame.payload {
        CsiPayload::EspRaw { layout, .. } => layout.indices().count(),
        _ => 0,
    };
    SOURCES.lock(|cell| {
        let mut list = cell.borrow_mut();
        if let Some(entry) = list.iter_mut().find(|e| e.mac == packet.mac()) {
            entry.count += 1;
            entry.rssi = packet.rssi() as i32;
            entry.ppdu = ppdu;
            entry.mapped = mapped;
            return;
        }
        let _ = list.push(SourceTally {
            mac: packet.mac(),
            count: 1,
            rssi: packet.rssi() as i32,
            ppdu,
            mapped,
        });
    });
}

async fn report_task() {
    let mut previous: heapless::Vec<([u8; 6], u32), MAX_SOURCES> = heapless::Vec::new();
    loop {
        Timer::after_secs(1).await;
        let snapshot = SOURCES.lock(|cell| cell.borrow().clone());
        if snapshot.is_empty() {
            println!(
                "No CSI yet — is anything transmitting on channel {}?",
                CHANNEL
            );
            continue;
        }
        for entry in snapshot.iter() {
            let last = previous
                .iter()
                .find(|(mac, _)| *mac == entry.mac)
                .map(|(_, c)| *c)
                .unwrap_or(0);
            println!(
                "{:02x}:{:02x}:{:02x}:{:02x}:{:02x}:{:02x}  {} CSI/s  total {}  RSSI {}  subcarriers {}  {}  layout maps {}",
                entry.mac[0],
                entry.mac[1],
                entry.mac[2],
                entry.mac[3],
                entry.mac[4],
                entry.mac[5],
                entry.count.wrapping_sub(last),
                entry.count,
                entry.rssi,
                SUBCARRIERS.load(core::sync::atomic::Ordering::Relaxed),
                ppdu_name(entry.ppdu),
                entry.mapped,
            );
        }
        previous.clear();
        for entry in snapshot.iter() {
            let _ = previous.push((entry.mac, entry.count));
        }
    }
}

/// Last capture's subcarrier count. `>= 100` (commonly ~117) confirms HT40 actually engaged;
/// ~53 or ~56 means it fell back to legacy or HT20. See `docs/bandwidth.md`.
static SUBCARRIERS: core::sync::atomic::AtomicU32 = core::sync::atomic::AtomicU32::new(0);

#[esp_rtos::main]
async fn main(spawner: Spawner) -> ! {
    let config = esp_hal::Config::default().with_cpu_clock(CpuClock::max());
    let peripherals = esp_hal::init(config);
    init_logger(spawner, LogMode::EspCsiTool);

    esp_alloc::heap_allocator!(#[esp_hal::ram(reclaimed)] size: 61440);

    let timg0 = TimerGroup::new(peripherals.TIMG0);
    esp_rtos::start(timg0.timer0, peripherals.FROM_CPU_INTR0);

    let config_radio = esp_radio::wifi::ControllerConfig::default();
    let wifi_controller = esp_radio::wifi::WifiController::new(peripherals.WIFI, config_radio)
        .expect("Failed to initialize Wi-Fi controller");
    let controller = WIFI_CONTROLLER.init(wifi_controller);

    println!(
        "Starting sniffer (observer, receiver) on channel {}",
        CHANNEL
    );

    let mut node_handle = CsiNodeClient::new();

    let password = Password::try_from(PASSWORD).unwrap();

    //let client_config = StationConfig::default()
    //    .with_ssid(SSID.try_into().expect("SSID longer than 32 bytes"))
    //    .with_authentication(esp_radio::wifi::AuthenticationMethodConfig::Wpa(password));
    //
    //let station_config = WifiStationConfig::new(client_config)
    //    .session(SESSION_ROLE)
    //    .reporting(REPORTING);

    let ap_radio_config = AccessPointConfig::default()
        .with_ssid(SSID.try_into().expect("SSID longer than 32 bytes"))
        .with_authentication(AuthenticationMethodConfig::Open)
        .with_channel(CHANNEL);
    let ap_config = WifiApConfig::new(ap_radio_config, CHANNEL, None)
        .with_lease_pool(LEASE_POOL)
        .with_sync_burst(SYNC_BURST)
        .reporting(REPORTING);

    println!(
        "Starting softAP (initiator) — SSID {}, channel {}, {} lease(s)",
        SSID, CHANNEL, LEASE_POOL
    );

    println!("Starting station on SSID {}", SSID);

    let csi_hardware = NodeHardware::new(controller);
    let mut setup = MeasurementSetup::new().acquisition(CsiConfig::default());
    if let Some(hz) = PING_RATE_HZ {
        setup = setup.periodicity_hz(hz);
    }
    let mut node = CsiNode::access_point(ap_config, setup, csi_hardware);
    node.set_protocol(if HE20 {
        esp_radio::wifi::Protocol::AX
    } else {
        esp_radio::wifi::Protocol::N
    });

    set_csi_callback(on_csi);
    let _ = &mut node_handle;
    join(node.run(), report_task()).await;

    loop {
        println!("Sniffer stopped");
        Timer::after(Duration::from_secs(5)).await;
    }
}
