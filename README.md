# PAMS — Predictive Asset Management System (freezer monitoring pilot)

Raspberry Pi edge node that reads walk-in freezer data over **BACnet** (MS/TP or IP),
scores compressor/thermal health with a machine-learning model, and streams the
results through **MQTT → Node-RED → InfluxDB → Grafana**.

## Architecture

```
 Freezer / Siemens BMS
        │  BACnet (MS/TP over RS-485, or BACnet/IP)
        ▼
 Raspberry Pi node ──► MQTT  pams/freezers/<unit>   (temperature, door, + any
        │                                             real soft-sensors it reads)
   ML scoring service (per-unit ensemble) ──► MQTT  pams/scored/<unit>
        │                                    (health, RUL, anomaly, channels…)
   Node-RED ──► InfluxDB (measurements: readings, ml_scores) ──► Grafana
        │
   Predator (Windows desktop cockpit) ──► reads MQTT + Pi gateway (:8090)
```

The Docker stack (MQTT, Node-RED, InfluxDB, Grafana) and the Python services run as
`systemd` units, so the whole pipeline survives reboots with no terminals open.

## BACnet points PAMS reads

**Always (per BMS node):**

| Function            | Default object (MS/TP) | Default object (IP) | Access |
| ------------------- | ---------------------- | ------------------- | ------ |
| Freezer temperature | `analog-input:1`       | `analogInput:0`     | read   |
| Door status         | `binary-input:1`       | `binaryInput:1`     | read   |
| PAMS health score   | `analog-value:50`      | `analogValue:2`     | write  |

**Optional real soft-sensors** (read only when mapped; each flows into MQTT and the
ML ingests it automatically as level + rate). Recognized names: `evaporator_temp`,
`return_air_temp`, `ambient_temp`, `condenser_temp`, `suction_pressure`,
`discharge_pressure`, `superheat`, `compressor_current`, `humidity`, `setpoint`,
`defrost_status`, `compressor_status`. Nothing is fabricated — unmapped sensors are
simply absent, and mapped points that don't actually read are skipped.

Map them **either way**:

- **Predator UI (recommended):** the **Points** view can **auto-discover** a
  device's objects (YABE-style scan on the Pi), suggest a channel name for each,
  and let you map them by hand or **Import CSV** from Excel, then **Save to Pi**.
  It writes `~/pams_points.json` live through the gateway and the BMS node picks
  it up automatically (no restart). It can also **Export ICC/FLN CSV** (the ICC
  Mirus mapping used to bridge points onto the Siemens FLN).
- **Env file:** set `PAMS_EXTRA_POINTS` in `deploy/systemd/pams.env`.

```bash
# deploy/systemd/pams.env  (MS/TP uses dashed objtypes; IP uses camelCase)
PAMS_EXTRA_POINTS=evaporator_temp=analog-input:2,suction_pressure=analog-input:3,defrost_status=binary-input:2
```

Channel names are **arbitrary** — the ML ingests any real object name (a chiller's
`c1_superheat`, `cond1_ref_pressure`, … not just the freezer names above). The
whole chain is **adaptive**: if 4 points report, the ML scores on 4 and InfluxDB
stores 4; if 40 report, 40 — automatically, with no config beyond the mapping.

Gateway endpoints backing this: `GET /api/scan?device=<id>` (object-list + names +
auto-suggest), `GET/POST /api/points-map` (read/save the mapping).

## Machine learning

Each unit gets its own ensemble — **IsolationForest** (always on), plus
**LSTM autoencoder**, **HMM**, and **XGBoost RUL** when their dependencies are
installed — fused into one 0–100 health score. The feature schema is **per-unit and
adaptive**: every active sensor contributes two model inputs (its level and its
rate of change), and the schema widens automatically the first time a new sensor
appears (models rebuild for the new dimensionality). History is stored per unit as
JSONL and a legacy CSV is migrated automatically.

Validate locally (needs `numpy` + `scikit-learn`):

```bash
PYTHONPATH=deploy python deploy/scripts/ml_selftest.py            # temperature-only
PYTHONPATH=deploy python deploy/scripts/ml_ensemble_selftest.py   # multi-sensor
```

## Documentation index

| Doc                                      | What it covers                                         |
| ---------------------------------------- | ------------------------------------------------------ |
| `README.md` (this file)                  | System overview, points, ML, quick start               |
| `predator/README.md`                     | The Windows desktop cockpit (connectivity, terminal)   |
| `deploy/PAMS_BACnet_MSTP_Integration.md` | Commissioning sheet for the BMS engineer (points list) |
| `deploy/PAMS_CheatSheet.txt`             | Operator quick reference                               |
| `deploy/PAMS_Commands.txt`               | Command reference                                      |
| In-app **Help & Docs** (Predator)        | Searchable, offline docs bundled in the app            |

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
- The Predator desktop app ships with a **real interactive terminal** (PowerShell +
  `ssh admin@alpha-p`) and searchable in-app docs; see `predator/README.md`.
