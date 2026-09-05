"""
PAMS BMS Pilot Node - BACnet MS/TP transport (Pi + RS-485 / FT232R).

Runs on the Raspberry Pi. Reads freezer temperature (and optional door) from a
Siemens BMS controller over the MS/TP trunk (/dev/ttyUSB0), computes the PAMS
health score, writes the score back to the BMS, and publishes the reading to
MQTT so it flows into Node-RED -> InfluxDB -> Grafana.

MS/TP I/O is handled by Steve Karg's bacnet-stack CLI tools (bacrp/bacwp), built
with the MS/TP datalink in ~/bacnet-stack/bin. This module is the transport twin
of bms_ip_node.py: identical scoring + MQTT logic.

Configure via environment variables (defaults in CONFIG below):
  BACNET_BIN        Path to bacnet-stack bin dir (default ~/bacnet-stack/bin)
  MSTP_IFACE        Serial device (default /dev/ttyUSB0)
  MSTP_BAUD         Trunk baud, must match the BMS (default 38400)
  MSTP_MAC          THIS node's MS/TP MAC, 1-127 (default 45)
  MSTP_MAX_MASTER   Highest master MAC on the trunk (default 127)
  TARGET_DEVICE     Siemens controller BACnet device instance (REQUIRED for real use)
  TARGET_MAC        Siemens controller MS/TP MAC in hex, e.g. 01 (optional but recommended)
  TEMP_OBJ          Temp object "type:instance"   (default analog-input:1)
  DOOR_OBJ          Door object "type:instance"   (default binary-input:1); DOOR_ENABLE=0 to skip
  SCORE_OBJ         Writable score "type:instance" (default analog-value:50); WRITE_ENABLE=0 to skip
  WRITE_PRIORITY    BACnet write priority 1-16 (default 8)
  UNIT_ID           Freezer id for MQTT/tagging (default FRZ-BMS-01)
  MQTT_HOST/PORT    Broker (default localhost:1883)
  POLL_SECONDS      Poll interval (default 5)
  APDU_TIMEOUT_MS   Per-request timeout in ms (default 3000)
"""

import os
import re
import json
import time
import subprocess

import paho.mqtt.client as mqtt


# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
def _split_obj(env_value, default):
    val = env_value or default
    t, i = val.split(":")
    return t, i  # keep as strings; bacnet-stack accepts type names + instance

HOME = os.path.expanduser("~")
BACNET_BIN = os.environ.get("BACNET_BIN", os.path.join(HOME, "bacnet-stack", "bin"))
BACRP = os.path.join(BACNET_BIN, "bacrp")
BACWP = os.path.join(BACNET_BIN, "bacwp")

MSTP_IFACE = os.environ.get("MSTP_IFACE", "/dev/ttyUSB0")
MSTP_BAUD = os.environ.get("MSTP_BAUD", "38400")
MSTP_MAC = os.environ.get("MSTP_MAC", "45")
MSTP_MAX_MASTER = os.environ.get("MSTP_MAX_MASTER", "127")

TARGET_DEVICE = os.environ.get("TARGET_DEVICE", "1")   # set to real Siemens device instance
TARGET_MAC = os.environ.get("TARGET_MAC", "")          # e.g. "01"; empty -> bind via Who-Is only

TEMP_TYPE, TEMP_INST = _split_obj(os.environ.get("TEMP_OBJ"), "analog-input:1")
DOOR_TYPE, DOOR_INST = _split_obj(os.environ.get("DOOR_OBJ"), "binary-input:1")
SCORE_TYPE, SCORE_INST = _split_obj(os.environ.get("SCORE_OBJ"), "analog-value:50")

DOOR_ENABLE = os.environ.get("DOOR_ENABLE", "1") == "1"
WRITE_ENABLE = os.environ.get("WRITE_ENABLE", "1") == "1"
WRITE_PRIORITY = os.environ.get("WRITE_PRIORITY", "8")

UNIT_ID = os.environ.get("UNIT_ID", "FRZ-BMS-01")
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "5"))
APDU_TIMEOUT_MS = os.environ.get("APDU_TIMEOUT_MS", "3000")

TOPIC = f"pams/freezers/{UNIT_ID}"

# Environment handed to every bacnet-stack tool invocation (selects MS/TP).
BAC_ENV = os.environ.copy()
BAC_ENV.update({
    "BACNET_DATALINK": "mstp",
    "BACNET_IFACE": MSTP_IFACE,
    "BACNET_MSTP_IFACE": MSTP_IFACE,
    "BACNET_MSTP_BAUD": MSTP_BAUD,
    "BACNET_MSTP_MAC": MSTP_MAC,
    "BACNET_MAX_MASTER": MSTP_MAX_MASTER,
    "BACNET_MAX_INFO_FRAMES": "1",
    "BACNET_APDU_TIMEOUT": APDU_TIMEOUT_MS,
})

_NUM_RE = re.compile(r"[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?")


# ----------------------------------------------------------------------------
# bacnet-stack tool wrappers
# ----------------------------------------------------------------------------
def _mac_args():
    return ["--mac", TARGET_MAC] if TARGET_MAC else []


def bac_read(obj_type, obj_inst, prop="present-value"):
    """Run bacrp, return stripped stdout or None on failure."""
    cmd = [BACRP, str(TARGET_DEVICE), obj_type, str(obj_inst), prop] + _mac_args()
    try:
        res = subprocess.run(cmd, env=BAC_ENV, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        print(f"  bacrp timeout: {obj_type} {obj_inst}")
        return None
    out = (res.stdout or "").strip()
    err = (res.stderr or "").strip()
    if res.returncode != 0 or not out:
        print(f"  bacrp failed ({obj_type} {obj_inst}) rc={res.returncode}: {err or out}")
        return None
    return out


def bac_write_real(obj_type, obj_inst, value, prop="present-value", priority=None):
    """Run bacwp writing a REAL (tag 4). Returns True on success."""
    pri = str(priority if priority is not None else WRITE_PRIORITY)
    cmd = [BACWP, str(TARGET_DEVICE), obj_type, str(obj_inst), prop, pri, "-1", "4",
           f"{value:.2f}"] + _mac_args()
    try:
        res = subprocess.run(cmd, env=BAC_ENV, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        print(f"  bacwp timeout: {obj_type} {obj_inst}")
        return False
    if res.returncode == 0:
        return True
    print(f"  bacwp failed ({obj_type} {obj_inst}) rc={res.returncode}: "
          f"{(res.stderr or res.stdout or '').strip()}")
    return False


def read_temp():
    out = bac_read(TEMP_TYPE, TEMP_INST)
    if out is None:
        return None
    m = _NUM_RE.search(out)
    return float(m.group()) if m else None


def read_door_open():
    out = bac_read(DOOR_TYPE, DOOR_INST)
    if out is None:
        return None
    low = out.lower()
    if "active" in low or low.strip() in ("1", "true", "on"):
        return True
    if "inactive" in low or low.strip() in ("0", "false", "off"):
        return False
    m = _NUM_RE.search(out)
    return (float(m.group()) != 0.0) if m else None


def compute_score(live_temp, door_open):
    score = 100.0
    if live_temp > -18.0:
        score -= (live_temp + 18.0) * 15
    if door_open:
        score -= 20
    return max(0.0, min(100.0, score))


# ----------------------------------------------------------------------------
# MQTT
# ----------------------------------------------------------------------------
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
mqtt_client.loop_start()


def poll_once():
    live_temp = read_temp()
    if live_temp is None:
        print("Skipping cycle: no temperature reading.")
        return

    door_open = False
    if DOOR_ENABLE:
        d = read_door_open()
        if d is not None:
            door_open = d

    score = compute_score(live_temp, door_open)

    wrote = False
    if WRITE_ENABLE:
        wrote = bac_write_real(SCORE_TYPE, SCORE_INST, score)

    payload = {
        "unit_id": UNIT_ID,
        "temperature": float(round(live_temp, 2)),
        "door_status": 1 if door_open else 0,
        "health_score": float(round(score, 1)),
        "ts": time.time(),
    }
    mqtt_client.publish(TOPIC, json.dumps(payload), qos=0)

    print(f"{UNIT_ID}  temp={round(live_temp, 2)}C  door={'OPEN' if door_open else 'closed'}  "
          f"score={round(score, 1)}  write={'ok' if wrote else ('skip' if not WRITE_ENABLE else 'FAIL')}  "
          f"-> {TOPIC}")


def main():
    print(f"PAMS BMS Node (BACnet MS/TP) on {MSTP_IFACE} @ {MSTP_BAUD} baud, our MAC={MSTP_MAC}")
    print(f"  target : device {TARGET_DEVICE}" + (f", MS/TP MAC {TARGET_MAC}" if TARGET_MAC else " (Who-Is bind)"))
    print(f"  temp   : {TEMP_TYPE}:{TEMP_INST}")
    print(f"  door   : {DOOR_TYPE}:{DOOR_INST}" if DOOR_ENABLE else "  door   : disabled")
    print(f"  score  : {SCORE_TYPE}:{SCORE_INST} (pri {WRITE_PRIORITY})" if WRITE_ENABLE else "  score  : write disabled")
    print(f"  mqtt   : {MQTT_HOST}:{MQTT_PORT} topic={TOPIC}")

    try:
        while True:
            try:
                poll_once()
            except Exception as e:
                print(f"Poll error: {e}")
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()


if __name__ == "__main__":
    main()
