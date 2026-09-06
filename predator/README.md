# Predator

Offline, dark-themed desktop cockpit for the **PAMS** project. Predator runs on any
Windows desktop and talks **only** to the PAMS host (`alpha-p`) over the local
network — no internet required, no cloud, no telemetry.

## What it does (MVP)

- Connects to the PAMS MQTT broker on `alpha-p:1883` and shows a **live unit
  dashboard**: temperature, door state, ML health score, RUL, thermal velocity,
  inferred state, anomaly/training flags, and a rolling temperature sparkline.
- Reads the streams PAMS already publishes (`pams/scored/#`, `pams/freezers/#`),
  so **it changes nothing on the Pi**.
- Per-desktop **Settings** (gear icon) to point at a different host/IP or ports.

Planned next phases: InfluxDB history charts, a BACnet device/point explorer
(YABE-style), config editing (ICC-style), and service control — all via a small
read/allow-listed gateway on the Pi.

## Requirements

- **To run/develop:** Node.js 18+ and internet **once** (to `npm install`).
- **To use the built app:** nothing — the installer is fully self-contained and
  works with zero internet on the LAN.

## Develop / run locally

```powershell
npm install
npm start
```

## Build installers for distribution

```powershell
npm run dist            # NSIS installer (.exe) + portable .exe in dist\
npm run dist:portable   # portable single-file .exe only
```

Output lands in `dist\`:

- `Predator Setup <version>.exe` — double-click installer for each desktop.
- `Predator <version>.exe` — portable, no install needed.

Copy either file to the target desktops. They need no internet and no
dependencies — Electron's runtime is bundled.

> First `npm run dist` downloads electron-builder's Windows packaging resources,
> so run it once on a machine with internet. The **resulting installer** is
> offline-ready.

## Connectivity (USB / Ethernet, whatever's plugged in)

Predator reaches the PAMS host over **whichever link is live** and fails over
automatically when you swap cables. It probes a prioritized list of endpoints and
connects to the first that answers on the MQTT port:

```
alpha-p.local   ← mDNS name, resolves over any transport (recommended)
alpha-p         ← plain hostname
192.168.1.112   ← WiFi/LAN IP fallback
169.254.7.1     ← direct-cable (link-local) fallback — see below
```

Because `alpha-p.local` (mDNS/Avahi, already running on the Pi) resolves over
**WiFi, a router, a direct Ethernet cable (link-local, no router needed), or a
USB-to-Ethernet adapter**, a single name covers every "plug something in" case.

### Direct-cable fallback (PC ↔ Pi, no router)

For a bulletproof direct cable, the Pi's `eth0` is given a fixed link-local
address via a netplan drop-in (keeps DHCP, doesn't touch WiFi/Docker/PAMS):

```
# /etc/netplan/99-eth0-fallback.yaml
network:
  version: 2
  ethernets:
    eth0:
      dhcp4: true
      optional: true
      addresses:
        - 169.254.7.1/16
```

A directly-cabled Windows PC self-assigns a `169.254.x.x` APIPA address, so both
ends share the `169.254.0.0/16` subnet and reach each other with zero PC setup —
`169.254.7.1` is in Predator's endpoint list as the guaranteed fallback.

> **Note on "USB":** alpha-p is a **Raspberry Pi 5**, whose USB ports are
> host-only — it cannot present itself as a USB network device (the Pi Zero/4
> "SSH-over-USB-cable" gadget trick is not available on Pi 5). To use a USB port,
> connect through a **USB-to-Ethernet adapter**; Predator then reaches it the same
> way as Ethernet via `alpha-p.local`. No configuration changes on the Pi.

## Configuration

Edit the endpoint list or ports anytime via the **⚙ Settings** dialog (one
endpoint per line, tried top-to-bottom). Settings are stored locally per desktop:

```
%APPDATA%\Predator\predator-config.json
```

## Security / isolation

- Renderer has a strict Content-Security-Policy and cannot make network calls;
  all network I/O happens in the main process to the configured PAMS host only.
- No external/CDN assets, fonts, analytics, or auto-update endpoints.
