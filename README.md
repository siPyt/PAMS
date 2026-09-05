# PAMS — Predictive Asset Management System (freezer monitoring pilot)

Raspberry Pi edge node that reads walk-in freezer data over **BACnet** (MS/TP or IP),
scores compressor/thermal health with a machine-learning model, and streams the
results through **MQTT → Node-RED → InfluxDB → Grafana**.

## Architecture

```
 Freezer / Siemens BMS
        │  BACnet (MS/TP over RS-485, or BACnet/IP)
        ▼
 Raspberry Pi node ──► MQTT  pams/freezers/<unit>   (raw temp, door)
        │
   ML scoring service (IsolationForest, per-unit) ──► MQTT  pams/scored/<unit>
        │
   Node-RED ──► InfluxDB (measurements: readings, ml_scores) ──► Grafana
```

The Docker stack (MQTT, Node-RED, InfluxDB, Grafana) and the Python services run as
`systemd` units, so the whole pipeline survives reboots with no terminals open.

## Contents (`deploy/`)

| File                                       | Purpose                                                                         |
| ------------------------------------------ | ------------------------------------------------------------------------------- |
| `pams_control.py`                          | Menu-driven control panel for the MS/TP node (baud sweep, discover, EPICS, run) |
| `pams_mstp.conf`                           | Editable MS/TP config (baud, MAC, device instance, object IDs)                  |
| `pams_ml.py`                               | Per-unit IsolationForest anomaly engine                                         |
| `pams_ml_service.py`                       | MQTT bridge: raw readings → ML score → scored stream                            |
| `bms_ip_node.py`                           | BACnet/IP BMS node (read → score → write → MQTT)                                |
| `bms_mstp_node.py`                         | BACnet MS/TP BMS node (via bacnet-stack tools)                                  |
| `node-red-data/flows.json`                 | Node-RED flow: MQTT → InfluxDB (raw + ml_scores)                                |
| `mosquitto/config/mosquitto.conf`          | MQTT broker config                                                              |
| `systemd/*.service`, `pams.env`            | Boot-persistent services                                                        |
| `scripts/`                                 | Setup, build (bacnet-stack), and validation scripts                             |
| `PAMS_BACnet_MSTP_Integration.md`          | Commissioning sheet for the BMS engineer                                        |
| `PAMS_CheatSheet.txt`, `PAMS_Commands.txt` | Operator quick references                                                       |

## Quick start (on the Pi)

```bash
# ML scoring service (systemd, boot-persistent):
sudo systemctl status pams-ml

# Real BMS over MS/TP — configure & run:
~/pams_env/bin/python ~/pams_control.py       # menu: baud sweep, discover, run
```

## Requirements

- Raspberry Pi (RS-485 USB adapter for MS/TP)
- Python venv with: `paho-mqtt`, `scikit-learn`, `numpy`, `pandas`, `bacpypes`
- `bacnet-stack` built from source (MS/TP CLI tools)
- Docker stack: Eclipse Mosquitto, Node-RED, InfluxDB 2.x, Grafana

## Notes

- BACnet MS/TP I/O uses Steve Karg's `bacnet-stack` CLI tools (`bacrp`/`bacwp`/`bacwi`).
- InfluxDB tokens are **not** stored in this repo — enter them in the Node-RED editor.
