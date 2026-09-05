#!/usr/bin/env python3
"""
PAMS MS/TP Control Panel
========================

One program to configure and operate the Raspberry Pi as a BACnet MS/TP node on
the Siemens BMS trunk. All tunables (MAC, baud, device instance, object IDs,
MQTT, ...) live in an editable config file (pams_mstp.conf). Change them here via
the menu, or edit the file directly.

Run interactively:      python3 pams_control.py
Run polling headless:   python3 pams_control.py run
Discover devices:       python3 pams_control.py discover
Dump a device's points: python3 pams_control.py epics <device-instance>

BACnet MS/TP I/O uses Steve Karg's bacnet-stack CLI tools (bacrp/bacwp/bacwi/
bacepics), built with the MS/TP datalink in ~/bacnet-stack/bin.
"""

import os
import re
import sys
import json
import time
import subprocess
import configparser

try:
    import paho.mqtt.client as mqtt
except Exception:
    mqtt = None  # MQTT optional; discovery/tests still work without it.


CONFIG_PATH = os.environ.get(
    "PAMS_CONFIG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "pams_mstp.conf")
)

DEFAULTS = {
    "serial": {
        "iface": "/dev/ttyUSB0", "baud": "38400", "our_mac": "45",
        "max_master": "127", "max_info_frames": "1", "apdu_timeout_ms": "3000",
    },
    "target": {"device_instance": "1", "mac": ""},
    "points": {
        "temp": "analog-input:1", "door": "binary-input:1", "door_enable": "true",
        "score": "analog-value:50", "write_enable": "true", "write_priority": "8",
    },
    "mqtt": {"host": "localhost", "port": "1883", "unit_id": "FRZ-BMS-01", "poll_seconds": "5"},
    "bacnet": {"bin": "~/bacnet-stack/bin"},
}

_NUM_RE = re.compile(r"[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?")
_TOTAL_RE = re.compile(r"Total Devices:\s*(\d+)")

# Common BACnet MS/TP baud rates, ordered most-likely first.
SWEEP_BAUDS = ["38400", "76800", "9600", "19200", "115200"]


# ---------------------------------------------------------------------------
# Config load / save
# ---------------------------------------------------------------------------
def load_config():
    cfg = configparser.ConfigParser()
    # seed defaults
    for section, kv in DEFAULTS.items():
        cfg[section] = dict(kv)
    if os.path.exists(CONFIG_PATH):
        cfg.read(CONFIG_PATH)
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        f.write("# PAMS MS/TP configuration (written by pams_control.py)\n")
        cfg.write(f)
    print(f"Saved -> {CONFIG_PATH}")


# ---------------------------------------------------------------------------
# bacnet-stack integration
# ---------------------------------------------------------------------------
def bin_dir(cfg):
    return os.path.expanduser(cfg["bacnet"]["bin"])


def tool(cfg, name):
    return os.path.join(bin_dir(cfg), name)


def bac_env(cfg):
    env = os.environ.copy()
    env.update({
        "BACNET_DATALINK": "mstp",
        "BACNET_IFACE": cfg["serial"]["iface"],
        "BACNET_MSTP_IFACE": cfg["serial"]["iface"],
        "BACNET_MSTP_BAUD": cfg["serial"]["baud"],
        "BACNET_MSTP_MAC": cfg["serial"]["our_mac"],
        "BACNET_MAX_MASTER": cfg["serial"]["max_master"],
        "BACNET_MAX_INFO_FRAMES": cfg["serial"]["max_info_frames"],
        "BACNET_APDU_TIMEOUT": cfg["serial"]["apdu_timeout_ms"],
    })
    return env


def _mac_args(cfg):
    m = cfg["target"]["mac"].strip()
    return ["--mac", m] if m else []


def run_tool(cfg, name, args, timeout=20, stream=False):
    cmd = [tool(cfg, name)] + [str(a) for a in args]
    if stream:
        return subprocess.run(cmd, env=bac_env(cfg), timeout=timeout).returncode, "", ""
    try:
        res = subprocess.run(cmd, env=bac_env(cfg), capture_output=True, text=True, timeout=timeout)
        return res.returncode, (res.stdout or "").strip(), (res.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def bac_read(cfg, obj, prop="present-value"):
    otype, oinst = obj.split(":")
    rc, out, err = run_tool(
        cfg, "bacrp",
        [cfg["target"]["device_instance"], otype, oinst, prop] + _mac_args(cfg),
        timeout=15,
    )
    if rc != 0 or not out:
        return None, (err or out or f"rc={rc}")
    return out, ""


def bac_write_real(cfg, obj, value, prop="present-value"):
    otype, oinst = obj.split(":")
    pri = cfg["points"]["write_priority"]
    rc, out, err = run_tool(
        cfg, "bacwp",
        [cfg["target"]["device_instance"], otype, oinst, prop, pri, "-1", "4", f"{value:.2f}"]
        + _mac_args(cfg),
        timeout=15,
    )
    return rc == 0, (err or out)


def parse_temp(text):
    m = _NUM_RE.search(text)
    return float(m.group()) if m else None


def parse_door(text):
    low = text.lower()
    if "active" in low or low.strip() in ("1", "true", "on"):
        return True
    if "inactive" in low or low.strip() in ("0", "false", "off"):
        return False
    m = _NUM_RE.search(text)
    return (float(m.group()) != 0.0) if m else None


def compute_score(temp, door_open):
    score = 100.0
    if temp > -18.0:
        score -= (temp + 18.0) * 15
    if door_open:
        score -= 20
    return max(0.0, min(100.0, score))


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
def parse_total_devices(out, err=""):
    m = _TOTAL_RE.search((out or "") + "\n" + (err or ""))
    return int(m.group(1)) if m else 0


def discover(cfg):
    print("Scanning MS/TP trunk for devices (Who-Is)... "
          f"[iface={cfg['serial']['iface']} baud={cfg['serial']['baud']} our_mac={cfg['serial']['our_mac']}]")
    rc, out, err = run_tool(cfg, "bacwi", [], timeout=15)
    print(out or "(no devices responded)")
    if err:
        print("stderr:", err)


def baud_sweep(cfg):
    """Try each common baud rate, run Who-Is, and report which one sees the trunk."""
    original = cfg["serial"]["baud"]
    results = {}
    print("Baud sweep - sending Who-Is at each rate (~12s each). "
          "Make sure the trunk is wired and powered.\n")
    try:
        for b in SWEEP_BAUDS:
            cfg["serial"]["baud"] = b
            rc, out, err = run_tool(cfg, "bacwi", [], timeout=15)
            n = parse_total_devices(out, err)
            results[b] = n
            flag = "  <-- devices!" if n > 0 else ""
            print(f"  baud {b:>6}: {n} device(s){flag}")
    finally:
        cfg["serial"]["baud"] = original  # restore unless user opts to save below

    best = max(results, key=results.get) if results else None
    if best and results[best] > 0:
        print(f"\nTrunk responds at baud {best} ({results[best]} device(s)).")
        if input(f"Set baud to {best} and save config? (y/N): ").strip().lower() == "y":
            cfg["serial"]["baud"] = best
            save_config(cfg)
            print("Tip: now run Discover (6) then EPICS (7) to read the device object list.")
    else:
        print("\nNo devices responded at any baud. Check:"
              "\n  - A/B polarity (try swapping the two data wires)"
              "\n  - Ground/reference connected"
              "\n  - End-of-line termination + bias present on the trunk"
              "\n  - The trunk is actually BACnet MS/TP (not P1/P2 FLN)"
              "\n  - Our MAC is unused and <= max_master")


def epics(cfg, device_instance=None):
    di = str(device_instance if device_instance is not None else cfg["target"]["device_instance"])
    print(f"Dumping object list (EPICS) for device {di} ... this can take a moment.")
    run_tool(cfg, "bacepics", [di] + _mac_args(cfg), timeout=90, stream=True)


def test_read(cfg):
    print(f"Reading temperature {cfg['points']['temp']} from device {cfg['target']['device_instance']} ...")
    out, err = bac_read(cfg, cfg["points"]["temp"])
    if out is None:
        print("  FAILED:", err)
        return
    t = parse_temp(out)
    print(f"  raw='{out}'  parsed={t} C")
    if cfg["points"]["door_enable"] == "true":
        dout, derr = bac_read(cfg, cfg["points"]["door"])
        print(f"  door raw='{dout}'  open={parse_door(dout) if dout else derr}")


def poll_loop(cfg):
    unit = cfg["mqtt"]["unit_id"]
    topic = f"pams/freezers/{unit}"
    door_enable = cfg["points"]["door_enable"] == "true"
    write_enable = cfg["points"]["write_enable"] == "true"
    poll_seconds = float(cfg["mqtt"]["poll_seconds"])

    client = None
    if mqtt is not None:
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            client.connect(cfg["mqtt"]["host"], int(cfg["mqtt"]["port"]), 60)
            client.loop_start()
        except Exception as e:
            print(f"(MQTT disabled: {e})")
            client = None

    print(f"Polling device {cfg['target']['device_instance']} every {poll_seconds}s. Ctrl-C to stop.")
    print(f"  temp={cfg['points']['temp']}  door={cfg['points']['door'] if door_enable else 'off'}  "
          f"score={cfg['points']['score'] if write_enable else 'write off'}  topic={topic}")
    try:
        while True:
            out, err = bac_read(cfg, cfg["points"]["temp"])
            if out is None:
                print(f"  read failed: {err}")
                time.sleep(poll_seconds)
                continue
            temp = parse_temp(out)
            if temp is None:
                print(f"  unparsable temp: '{out}'")
                time.sleep(poll_seconds)
                continue

            door_open = False
            if door_enable:
                dout, _ = bac_read(cfg, cfg["points"]["door"])
                if dout is not None:
                    d = parse_door(dout)
                    if d is not None:
                        door_open = d

            score = compute_score(temp, door_open)
            wrote = False
            if write_enable:
                wrote, werr = bac_write_real(cfg, cfg["points"]["score"], score)
                if not wrote:
                    print(f"  write failed: {werr}")

            if client is not None:
                payload = {
                    "unit_id": unit,
                    "temperature": float(round(temp, 2)),
                    "door_status": 1 if door_open else 0,
                    "health_score": float(round(score, 1)),
                    "ts": time.time(),
                }
                client.publish(topic, json.dumps(payload), qos=0)

            print(f"  {unit}  temp={round(temp,2)}C  door={'OPEN' if door_open else 'closed'}  "
                  f"score={round(score,1)}  write={'ok' if wrote else ('off' if not write_enable else 'FAIL')}")
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if client is not None:
            client.loop_stop()
            client.disconnect()


# ---------------------------------------------------------------------------
# Interactive menu
# ---------------------------------------------------------------------------
def _prompt(label, current):
    val = input(f"  {label} [{current}]: ").strip()
    return val if val else current


def show_config(cfg):
    print("\n--- Current configuration ---")
    for section in cfg.sections():
        print(f"[{section}]")
        for k, v in cfg[section].items():
            print(f"  {k} = {v}")
    print(f"(file: {CONFIG_PATH})\n")


def edit_serial(cfg):
    s = cfg["serial"]
    print("\nEdit serial / MS/TP (Enter keeps current):")
    s["iface"] = _prompt("serial device", s["iface"])
    s["baud"] = _prompt("baud (9600/19200/38400/76800/115200)", s["baud"])
    s["our_mac"] = _prompt("this Pi MS/TP MAC (0-127)", s["our_mac"])
    s["max_master"] = _prompt("max_master", s["max_master"])
    s["max_info_frames"] = _prompt("max_info_frames", s["max_info_frames"])
    s["apdu_timeout_ms"] = _prompt("apdu timeout (ms)", s["apdu_timeout_ms"])


def edit_target(cfg):
    t = cfg["target"]
    print("\nEdit target controller (Enter keeps current):")
    t["device_instance"] = _prompt("Siemens device instance", t["device_instance"])
    t["mac"] = _prompt("Siemens MS/TP MAC hex (blank=Who-Is bind)", t["mac"])


def edit_points(cfg):
    p = cfg["points"]
    print("\nEdit points (type:instance). Enter keeps current:")
    p["temp"] = _prompt("temperature object", p["temp"])
    p["door"] = _prompt("door object", p["door"])
    p["door_enable"] = _prompt("door_enable (true/false)", p["door_enable"])
    p["score"] = _prompt("score object (writable)", p["score"])
    p["write_enable"] = _prompt("write_enable (true/false)", p["write_enable"])
    p["write_priority"] = _prompt("write priority (1-16)", p["write_priority"])


def edit_mqtt(cfg):
    m = cfg["mqtt"]
    print("\nEdit MQTT / polling (Enter keeps current):")
    m["host"] = _prompt("MQTT host", m["host"])
    m["port"] = _prompt("MQTT port", m["port"])
    m["unit_id"] = _prompt("unit id (MQTT topic/tag)", m["unit_id"])
    m["poll_seconds"] = _prompt("poll interval (s)", m["poll_seconds"])


def menu():
    cfg = load_config()
    actions = {
        "1": ("Show current config", lambda: show_config(cfg)),
        "2": ("Edit serial / MS/TP (iface, baud, MAC)", lambda: edit_serial(cfg)),
        "3": ("Edit target controller (device instance, MAC)", lambda: edit_target(cfg)),
        "4": ("Edit points & write-back", lambda: edit_points(cfg)),
        "5": ("Edit MQTT & polling", lambda: edit_mqtt(cfg)),
        "6": ("Discover devices on trunk (Who-Is)", lambda: discover(cfg)),
        "b": ("Baud sweep (auto-find trunk baud)", lambda: baud_sweep(cfg)),
        "7": ("Dump a device's objects (EPICS)", lambda: epics(cfg, input("  device instance: ").strip() or None)),
        "8": ("Test read temperature once", lambda: test_read(cfg)),
        "9": ("Start polling (Ctrl-C to stop)", lambda: poll_loop(cfg)),
        "s": ("Save config", lambda: save_config(cfg)),
        "q": ("Quit", None),
    }
    while True:
        print("\n===== PAMS MS/TP Control Panel =====")
        for key in ["1", "2", "3", "4", "5", "6", "b", "7", "8", "9", "s", "q"]:
            print(f"  {key}) {actions[key][0]}")
        choice = input("Select: ").strip().lower()
        if choice == "q":
            print("Bye.")
            return
        action = actions.get(choice)
        if not action:
            print("Unknown option.")
            continue
        try:
            action[1]()
        except Exception as e:
            print(f"Error: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    args = sys.argv[1:]
    if not args:
        menu()
        return
    cfg = load_config()
    cmd = args[0].lower()
    if cmd == "run":
        poll_loop(cfg)
    elif cmd == "discover":
        discover(cfg)
    elif cmd == "sweep":
        baud_sweep(cfg)
    elif cmd == "epics":
        epics(cfg, args[1] if len(args) > 1 else None)
    elif cmd == "test":
        test_read(cfg)
    else:
        print(f"Unknown command '{cmd}'. Use: run | discover | sweep | epics <inst> | test  (or no args for menu)")


if __name__ == "__main__":
    main()
