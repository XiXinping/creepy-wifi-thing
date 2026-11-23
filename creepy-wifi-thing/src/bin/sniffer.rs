#![no_std]
#![no_main]

use embassy_executor::Spawner;
use embassy_futures::join::join;
use embassy_sync::blocking_mutex::Mutex;
use embassy_sync::blocking_mutex::raw::CriticalSectionRawMutex;
use embassy_time::{Duration, Timer};
use defmt_rtt as _;
use esp_csi_rs::csi::CsiPacket;
use esp_csi_rs::logging::logging::{LogMode, init_logger, log_csi};
use esp_csi_rs::wire::{CsiPayload, PpduFormat};
use esp_csi_rs::{
    CsiConfig, CsiNode, MeasurementSetup, NodeHardware, Reporting, WifiSnifferConfig,
    set_csi_callback,
};
use esp_hal::clock::CpuClock;
use esp_hal::timer::timg::TimerGroup;
use esp_radio::wifi::WifiController;
use {esp_backtrace as _, esp_println as _};

extern crate alloc;
mod sniffer_control;

/// Primary channel to monitor. On the ESP32-C5, channels >= 36 select 5 GHz.
/// The ESP32-C6 supports only 2.4 GHz channels.
const CHANNEL: u8 = 11;

/// `Report { to }`, `ThresholdBased(..)` or `Decimate(n)`. A threshold must be calibrated: run
/// with `Threshold::new(level, hold_ms).variation_only()` first and read the scores a still room
/// gives.
const REPORTING: Reporting = Reporting::HOST;

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
    // Count of complex values reported by esp-csi-rs for this capture.
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
            esp_println::println!(
                "No CSI yet — is anything transmitting on channel {}?",
                sniffer_control::channel()
            );
            continue;
        }
        for entry in snapshot.iter() {
            let last = previous
                .iter()
                .find(|(mac, _)| *mac == entry.mac)
                .map(|(_, c)| *c)
                .unwrap_or(0);
            esp_println::println!(
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
    let usb = esp_hal::usb::usb_serial_jtag::UsbSerialJtag::new(peripherals.USB_DEVICE);
    init_logger(spawner, LogMode::EspCsiTool);

    esp_alloc::heap_allocator!(#[esp_hal::ram(reclaimed)] size: 61440);

    let timg0 = TimerGroup::new(peripherals.TIMG0);
    esp_rtos::start(timg0.timer0, peripherals.FROM_CPU_INTR0);

    let config_radio = esp_radio::wifi::ControllerConfig::default();
    let wifi_controller = esp_radio::wifi::WifiController::new(peripherals.WIFI, config_radio)
        .expect("Failed to initialize Wi-Fi controller");
    let controller = WIFI_CONTROLLER.init(wifi_controller);

    esp_println::println!(
        "Starting sniffer (observer, receiver) on channel {}",
        CHANNEL
    );

    let csi_hardware = NodeHardware::new(controller);
    let mut sniffer = WifiSnifferConfig::default().with_channel(CHANNEL);
    match REPORTING {
        Reporting::Report { to } => sniffer = sniffer.with_report_to(to),
        Reporting::ThresholdBased(threshold) => sniffer = sniffer.with_threshold(threshold),
        Reporting::Decimate(n) => sniffer = sniffer.with_decimation(n),
        _ => {}
    }
    let acquisition = if HE20 {
        CsiConfig::he20()
    } else {
        CsiConfig::default()
    };
    let setup = MeasurementSetup::new().acquisition(acquisition);
    let mut node = CsiNode::sniffer(sniffer, setup, csi_hardware);
    node.set_protocol(if HE20 {
        esp_radio::wifi::Protocol::AX
    } else {
        esp_radio::wifi::Protocol::N
    });

    set_csi_callback(on_csi);
    spawner.spawn(sniffer_control::control_task(usb).unwrap());
    join(node.run(), report_task()).await;

    loop {
        esp_println::println!("Sniffer stopped");
        Timer::after(Duration::from_secs(5)).await;
    }
}
