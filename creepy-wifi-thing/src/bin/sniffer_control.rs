//! Passive 802.11 identity observations and USB channel commands.

use core::{cell::RefCell, fmt::Write, sync::atomic::{AtomicU8, Ordering}};
use embassy_sync::blocking_mutex::{Mutex, raw::CriticalSectionRawMutex};
use embassy_time::{Duration, Timer};
use esp_hal::{Blocking, usb::usb_serial_jtag::UsbSerialJtag};
use heapless::{String, Vec};

#[cfg(feature = "esp32c5")]
use esp_wifi_sys_esp32c5::include as sys;
#[cfg(feature = "esp32c6")]
use esp_wifi_sys_esp32c6::include as sys;

const MAX_APS: usize = 24;
const MAX_STATIONS: usize = 48;
static CHANNEL: AtomicU8 = AtomicU8::new(11);

#[derive(Clone)]
struct AccessPoint { bssid: [u8; 6], ssid: [u8; 32], length: u8 }
#[derive(Clone, Copy)]
struct Station { mac: [u8; 6], bssid: [u8; 6] }

static APS: Mutex<CriticalSectionRawMutex, RefCell<Vec<AccessPoint, MAX_APS>>> =
    Mutex::new(RefCell::new(Vec::new()));
static STATIONS: Mutex<CriticalSectionRawMutex, RefCell<Vec<Station, MAX_STATIONS>>> =
    Mutex::new(RefCell::new(Vec::new()));

fn address(frame: &[u8], offset: usize) -> [u8; 6] {
    let mut mac = [0; 6];
    mac.copy_from_slice(&frame[offset..offset + 6]);
    mac
}

fn observe(frame: &[u8]) {
    if frame.len() < 24 { return; }
    let kind = (frame[0] >> 2) & 3;
    let subtype = frame[0] >> 4;
    if kind == 0 && (subtype == 8 || subtype == 5) && frame.len() >= 38 {
        // Beacon/probe response: fixed parameters occupy bytes 24..36.
        let bssid = address(frame, 16);
        let mut offset = 36;
        while offset + 2 <= frame.len() {
            let tag = frame[offset];
            let length = frame[offset + 1] as usize;
            offset += 2;
            if offset + length > frame.len() { break; }
            if tag == 0 {
                if (1..=32).contains(&length) && frame[offset..offset + length].iter().any(|&b| b != 0) {
                    APS.lock(|cell| {
                        let mut aps = cell.borrow_mut();
                        let mut ssid = [0; 32];
                        ssid[..length].copy_from_slice(&frame[offset..offset + length]);
                        if let Some(ap) = aps.iter_mut().find(|ap| ap.bssid == bssid) {
                            ap.ssid = ssid; ap.length = length as u8;
                        } else {
                            let _ = aps.push(AccessPoint { bssid, ssid, length: length as u8 });
                        }
                    });
                }
                break;
            }
            offset += length;
        }
    }

    // Data headers expose the BSSID even when the payload is encrypted.
    let (station, bssid) = if kind == 2 {
        match (frame[1] & 1 != 0, frame[1] & 2 != 0) {
            (true, false) => (address(frame, 10), address(frame, 4)),
            (false, true) => (address(frame, 4), address(frame, 10)),
            _ => return,
        }
    } else if kind == 0 && (subtype == 0 || subtype == 2) {
        // Association/reassociation request.
        (address(frame, 10), address(frame, 4))
    } else { return; };
    if station[0] & 1 != 0 || bssid[0] & 1 != 0 { return; }
    STATIONS.lock(|cell| {
        let mut stations = cell.borrow_mut();
        if let Some(entry) = stations.iter_mut().find(|entry| entry.mac == station) {
            entry.bssid = bssid;
        } else {
            let _ = stations.push(Station { mac: station, bssid });
        }
    });
}

unsafe extern "C" fn on_frame(buf: *mut core::ffi::c_void, kind: sys::wifi_promiscuous_pkt_type_t) {
    if buf.is_null() || (kind != sys::wifi_promiscuous_pkt_type_t_WIFI_PKT_MGMT
        && kind != sys::wifi_promiscuous_pkt_type_t_WIFI_PKT_DATA) { return; }
    let packet = buf as *const sys::wifi_promiscuous_pkt_t;
    let length = unsafe { (*packet).rx_ctrl.sig_len() as usize };
    if !(24..=2304).contains(&length) { return; }
    let payload = unsafe { (buf as *const u8).add(core::mem::size_of::<sys::wifi_pkt_rx_ctrl_t>()) };
    observe(unsafe { core::slice::from_raw_parts(payload, length) });
}

pub fn channel() -> u8 { CHANNEL.load(Ordering::Relaxed) }

pub fn valid_channel(channel: u8) -> bool {
    if (1..=11).contains(&channel) { return true; }
    #[cfg(feature = "esp32c5")]
    { matches!(channel, 36 | 40 | 44 | 48 | 52 | 56 | 60 | 64 | 100 | 104 | 108 | 112 | 116 | 120 | 124 | 128 | 132 | 136 | 140 | 144 | 149 | 153 | 157 | 161 | 165) }
    #[cfg(feature = "esp32c6")]
    { false }
}

fn set_channel(channel: u8) {
    if !valid_channel(channel) { esp_println::println!("CHANNEL_ERROR,invalid"); return; }
    #[cfg(feature = "esp32c5")]
    {
        let band = if channel >= 36 { sys::wifi_band_mode_t_WIFI_BAND_MODE_5G_ONLY }
            else { sys::wifi_band_mode_t_WIFI_BAND_MODE_2G_ONLY };
        if unsafe { sys::esp_wifi_set_band_mode(band) } != 0 {
            esp_println::println!("CHANNEL_ERROR,band"); return;
        }
    }
    if unsafe { sys::esp_wifi_set_channel(channel, sys::wifi_second_chan_t_WIFI_SECOND_CHAN_NONE) } != 0 {
        esp_println::println!("CHANNEL_ERROR,radio"); return;
    }
    CHANNEL.store(channel, Ordering::Relaxed);
    APS.lock(|cell| cell.borrow_mut().clear());
    STATIONS.lock(|cell| cell.borrow_mut().clear());
    esp_println::println!("CHANNEL,{}", channel);
}

fn mac_text(mac: [u8; 6], output: &mut String<128>) {
    for (index, byte) in mac.iter().enumerate() {
        if index > 0 { let _ = output.push(':'); }
        let _ = write!(output, "{byte:02X}");
    }
}

fn report_identity() {
    esp_println::println!("CHANNEL,{}", channel());
    let aps = APS.lock(|cell| cell.borrow().clone());
    let stations = STATIONS.lock(|cell| cell.borrow().clone());
    for ap in aps {
        let mut line = String::<128>::new();
        let _ = line.push_str("AP_INFO,");
        mac_text(ap.bssid, &mut line);
        let _ = line.push(',');
        for byte in &ap.ssid[..ap.length as usize] { let _ = write!(line, "{byte:02X}"); }
        esp_println::println!("{line}");
    }
    for station in stations {
        let mut line = String::<128>::new();
        let _ = line.push_str("STA_INFO,");
        mac_text(station.mac, &mut line);
        let _ = line.push(',');
        mac_text(station.bssid, &mut line);
        esp_println::println!("{line}");
    }
}

#[embassy_executor::task]
pub async fn control_task(mut usb: UsbSerialJtag<'static, Blocking>) {
    // CsiNode starts promiscuous mode in its own task.
    Timer::after(Duration::from_secs(2)).await;
    let result = unsafe { sys::esp_wifi_set_promiscuous_rx_cb(Some(on_frame)) };
    esp_println::println!("CHANNEL,{}", channel());
    if result != 0 { esp_println::println!("IDENTITY_ERROR,callback"); }
    let mut command = [0u8; 12];
    let mut used = 0usize;
    let mut ticks = 0u16;
    loop {
        while let Ok(byte) = usb.read_byte() {
            if byte == b'\n' || byte == b'\r' {
                if command[..used].starts_with(b"CH ") {
                    if let Ok(text) = core::str::from_utf8(&command[3..used]) {
                        if let Ok(requested) = text.parse::<u8>() { set_channel(requested); }
                    }
                }
                used = 0;
            } else if used < command.len() { command[used] = byte; used += 1; }
            else { used = 0; }
        }
        ticks += 1;
        if ticks >= 250 { report_identity(); ticks = 0; }
        Timer::after_millis(20).await;
    }
}
