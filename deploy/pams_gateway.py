#!/usr/bin/env python3
"""
PAMS Gateway — minimal, read-only HTTP API for the Predator app.

Exposes REAL data that Predator's Services / Devices / Points views consume:
  GET  /api/health              -> liveness probe
  GET  /api/services            -> docker containers + systemd unit states
  GET  /api/capabilities        -> transports/interfaces PAMS can reach + protocols
  GET  /api/devices             -> best-effort BACnet Who-Is discovery
  GET  /api/points?device=<id>  -> best-effort BACnet object reads for a device
  GET  /api/scan?device=<id>    -> YABE-style object-list + names + auto-suggest
  GET  /api/bus-scan            -> auto-find MS/TP baud (Who-Is sweep) + devices
  GET  /api/ip-scan             -> BACnet/IP Who-Is (LAN broadcast) + devices
  GET  /api/discover-all        -> plug&play: every serial port + interface, all devices
  GET  /api/points-map          -> current BMS soft-sensor object mapping
  POST /api/points-map          -> save the mapping (~/pams_points.json)
  POST /api/monitor             -> auto-start a BACnet/IP poller for a device

Runs as the normal user (no root needed): it only reads `docker`/`systemctl`
status and runs the bacnet-stack CLI tools, and writes one JSON file in $HOME.
Stdlib only — no pip installs.

Start:   python3 ~/pams_gateway.py         (listens on :8090)
Env:     PAMS_GATEWAY_PORT (default 8090)
         PAMS_BACNET_BIN   (default ~/bacnet-stack/bin)
         PAMS_POINTS_FILE  (default ~/pams_points.json)
"""

import json
import os
import re
import subprocess
import time
import contextlib
import configparser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("PAMS_GATEWAY_PORT", "8090"))
BACNET_BIN = os.path.expanduser(os.environ.get("PAMS_BACNET_BIN", "~/bacnet-stack/bin"))
POINTS_FILE = os.path.expanduser(os.environ.get("PAMS_POINTS_FILE", "~/pams_points.json"))
MSTP_CONF = os.path.expanduser(os.environ.get("PAMS_CONFIG", "~/pams_mstp.conf"))
SYSTEMD_UNITS = ["pams-ml", "pams-bms"]

# BACnet/IP is handled by a bacpypes helper (venv python) because the bundled
# bacnet-stack CLI tools are compiled MS/TP-only. See pams_bacnet_ip.py.
VENV_PY = os.path.expanduser(os.environ.get("PAMS_VENV_PY", "~/pams_env/bin/python"))
IP_HELPER = os.path.expanduser(os.environ.get("PAMS_BACNET_IP_HELPER", "~/pams_bacnet_ip.py"))

# Common BACnet MS/TP baud rates, most-likely first (matches pams_control.py).
SWEEP_BAUDS = ["38400", "76800", "9600", "19200", "115200"]
_TOTAL_RE = re.compile(r"Total Devices:\s*(\d+)")

# Recognized soft-sensor channels (must match EXTRA_SENSOR_ORDER in pams_ml.py).
KNOWN_POINTS = [
    "temperature", "door_status",
    "evaporator_temp", "return_air_temp", "ambient_temp", "condenser_temp",
    "suction_pressure", "discharge_pressure", "superheat",
    "compressor_current", "humidity", "setpoint",
    "defrost_status", "compressor_status",
]
_OBJ_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:\d+$")

_cache = {}


def run(cmd, timeout=6, env=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", "not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:  # noqa: BLE001
        return 1, "", str(e)


def mstp_cfg():
    """Load serial/target settings from pams_mstp.conf (with safe defaults)."""
    cfg = configparser.ConfigParser()
    cfg["serial"] = {
        "iface": "/dev/ttyUSB0", "baud": "38400", "our_mac": "45",
        "max_master": "127", "max_info_frames": "1", "apdu_timeout_ms": "3000",
    }
    cfg["target"] = {"device_instance": "1", "mac": ""}
    try:
        if os.path.exists(MSTP_CONF):
            cfg.read(MSTP_CONF)
    except Exception:  # noqa: BLE001
        pass
    return cfg


def mstp_env(baud=None):
    return bacnet_env("mstp", baud)


def bacnet_env(datalink="mstp", baud=None, iface=None):
    """Environment that selects the datalink for bacnet-stack tools.
    datalink='mstp' (RS-485 trunk) or 'bip' (BACnet/IP over the LAN).
    `iface` overrides the serial port (mstp) or network interface (bip)."""
    cfg = mstp_cfg()
    env = os.environ.copy()
    env["BACNET_APDU_TIMEOUT"] = cfg["serial"]["apdu_timeout_ms"]
    if datalink == "bip":
        env["BACNET_DATALINK"] = "bip"
        ip_iface = iface or os.environ.get("PAMS_BIP_IFACE", "")
        if not ip_iface and cfg.has_section("bip"):
            ip_iface = cfg["bip"].get("iface", "")
        if ip_iface:
            env["BACNET_IFACE"] = ip_iface
        port = os.environ.get("PAMS_BIP_PORT", "")
        if not port and cfg.has_section("bip"):
            port = cfg["bip"].get("port", "")
        env["BACNET_IP_PORT"] = port or "47808"
    else:
        s = cfg["serial"]
        dev = iface or s["iface"]
        env.update({
            "BACNET_DATALINK": "mstp",
            "BACNET_IFACE": dev,
            "BACNET_MSTP_IFACE": dev,
            "BACNET_MSTP_BAUD": str(baud or s["baud"]),
            "BACNET_MSTP_MAC": s["our_mac"],
            "BACNET_MAX_MASTER": s["max_master"],
            "BACNET_MAX_INFO_FRAMES": s["max_info_frames"],
        })
    return env


def save_mstp_baud(baud):
    """Persist a discovered baud to pams_mstp.conf so later scans use it."""
    cfg = mstp_cfg()
    cfg["serial"]["baud"] = str(baud)
    try:
        with open(MSTP_CONF, "w") as f:
            f.write("# PAMS MS/TP configuration (written by pams_gateway.py)\n")
            cfg.write(f)
        return True
    except Exception:  # noqa: BLE001
        return False


def _parse_devices(text):
    """Pull device instance numbers from bacwi (Who-Is) output."""
    devices = []
    for line in (text or "").splitlines():
        low = line.lower()
        if "device" in low and "mac" in low:
            continue  # header row
        if "total devices" in low:
            continue
        m = re.match(r"\s*(\d{1,7})\b", line)
        if m:
            inst = int(m.group(1))
            if inst not in devices:
                devices.append(inst)
    return devices


def get_bus_scan():
    """Auto-find the trunk baud: send Who-Is at each common rate, report devices,
    and persist the winning baud. Slow (a few seconds per rate)."""
    tool = os.path.join(BACNET_BIN, "bacwi")
    if not os.path.exists(tool):
        return {"results": [], "note": "bacnet-stack tools not installed", "ts": time.time()}
    results = []
    best = None
    for b in SWEEP_BAUDS:
        rc, so, se = run([tool], timeout=12, env=mstp_env(b))
        n = int(_TOTAL_RE.search((so or "") + "\n" + (se or "")).group(1)) if _TOTAL_RE.search((so or "") + "\n" + (se or "")) else 0
        devs = _parse_devices(so)
        if not n and devs:
            n = len(devs)
        results.append({"baud": b, "count": n, "devices": devs})
        if n > 0 and best is None:
            best = b
    saved = save_mstp_baud(best) if best else False
    note = "" if best else "no devices responded at any baud (check wiring / A-B polarity / termination)"
    return {"results": results, "best_baud": best, "saved": saved, "note": note, "ts": time.time()}


def cached(key, ttl, producer):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = producer()
    _cache[key] = (now, val)
    return val


def _running_pollers():
    """Running IP pollers as (unit, device) parsed from their argv."""
    out = []
    rc, so, _ = run(["pgrep", "-af", "pams_ip_poller.py"], timeout=5)
    if rc == 0 and so:
        for line in so.splitlines():
            m = re.search(r"pams_ip_poller\.py\s+(\S+)\s+(\d+)", line)
            if m:
                out.append((m.group(1), int(m.group(2))))
    return out


def _launch_poller(unit, device):
    unit_pts = os.path.expanduser(f"~/pams_points_{unit}.json")
    log = os.path.expanduser(f"~/pams_poller_{unit}.log")
    env = os.environ.copy()
    if os.path.exists(unit_pts):
        env["PAMS_POINTS_FILE"] = unit_pts
    try:
        with open(log, "ab") as lf:
            subprocess.Popen(["setsid", VENV_PY, "-u", POLLER, unit, str(device)],
                             env=env, stdout=lf, stderr=lf, stdin=subprocess.DEVNULL,
                             start_new_session=True)
    except Exception:  # noqa: BLE001
        pass


@contextlib.contextmanager
def _pollers_paused():
    """Free UDP 47808 for discovery, then resume monitoring. YABE-style sims reply
    to the standard port 47808, so a running poller (which owns it) blocks discovery;
    we briefly stop pollers, discover, then relaunch them."""
    active = _running_pollers()
    if active:
        run(["pkill", "-f", "pams_ip_poller.py"], timeout=5)
        time.sleep(1.5)
    try:
        yield
    finally:
        for unit, device in active:
            _launch_poller(unit, device)


def run_ip_helper(args, timeout=20):
    """Call the bacpypes BACnet/IP helper (venv). Returns parsed JSON or None.
    Pauses any running pollers so the helper can bind port 47808 for discovery."""
    if not (os.path.exists(VENV_PY) and os.path.exists(IP_HELPER)):
        return None
    with _pollers_paused():
        try:
            r = subprocess.run([VENV_PY, IP_HELPER] + [str(a) for a in args],
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"error": "timeout", "devices": [], "objects": []}
        except Exception:  # noqa: BLE001
            return None
    for line in reversed((r.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                return None
    return None


def get_services():
    out = []
    rc, so, _ = run(
        ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Image}}\t{{.State}}\t{{.Status}}"]
    )
    if rc == 0 and so:
        for line in so.splitlines():
            p = line.split("\t")
            if len(p) >= 4:
                out.append(
                    {"name": p[0], "kind": "container", "state": p[2], "detail": f"{p[1]} — {p[3]}"}
                )
    for unit in SYSTEMD_UNITS:
        _, state, _ = run(["systemctl", "is-active", unit])
        _, desc, _ = run(["systemctl", "show", "-p", "Description", "--value", unit])
        out.append(
            {"name": unit, "kind": "systemd", "state": state or "unknown", "detail": desc or ""}
        )
    return {"services": out, "ts": time.time()}


def get_capabilities():
    """Discover what PAMS can physically reach + which protocols it can speak,
    so Predator can show 'what I have access to' and adapt discovery to it."""
    import glob
    ports = sorted(
        p for pat in ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/ttyAMA*", "/dev/ttyS[0-9]*")
        for p in glob.glob(pat)
    )
    interfaces = []
    rc, so, _ = run(["ip", "-o", "-4", "addr", "show"])
    if rc == 0:
        for line in so.splitlines():
            parts = line.split()
            name = parts[1] if len(parts) > 1 else ""
            cidr = ""
            for i, tok in enumerate(parts):
                if tok == "inet" and i + 1 < len(parts):
                    cidr = parts[i + 1]
            if name and cidr and name != "lo":
                # Skip container/virtual bridges - not real field networks.
                if name.startswith(("docker", "br-", "veth")):
                    continue
                interfaces.append({"iface": name, "cidr": cidr})
    tools_ok = all(os.path.exists(os.path.join(BACNET_BIN, t)) for t in ("bacwi", "bacrp"))
    protocols = [
        {
            "name": "BACnet MS/TP", "transport": "RS-485 serial",
            "available": bool(ports and tools_ok),
            "why": ("serial port + bacnet-stack present" if (ports and tools_ok)
                    else ("no serial port detected" if not ports else "bacnet-stack tools missing")),
            "discover": "bus-scan",
        },
        {
            "name": "BACnet/IP", "transport": "Ethernet / WiFi LAN",
            "available": bool(interfaces and tools_ok),
            "why": ("network + bacnet-stack present" if (interfaces and tools_ok)
                    else ("no network interface" if not interfaces else "bacnet-stack tools missing")),
            "discover": "ip-scan",
        },
    ]
    return {
        "serial_ports": ports, "interfaces": interfaces,
        "bacnet_tools": tools_ok, "protocols": protocols, "ts": time.time(),
    }


def get_devices(datalink="mstp", iface=None):
    if datalink == "bip":
        res = run_ip_helper(["whois"], timeout=12)
        if res is not None:
            return {"devices": res.get("devices", []), "datalink": "bip",
                    "note": res.get("note", ""), "ts": time.time()}
        return {"devices": [], "note": "BACnet/IP helper unavailable", "ts": time.time()}
    tool = os.path.join(BACNET_BIN, "bacwi")
    if not os.path.exists(tool):
        return {"devices": [], "note": "bacnet-stack tools not installed", "ts": time.time()}
    rc, so, se = run([tool], timeout=12, env=bacnet_env(datalink, iface=iface))
    devices = [{"instance": i} for i in _parse_devices(so)]
    note = "" if devices else "no BACnet devices responded (check baud / wiring)"
    if rc == 124:
        note = "discovery timed out"
    return {"devices": devices, "datalink": datalink, "note": note, "ts": time.time()}


def get_ip_scan(target=None):
    """BACnet/IP Who-Is via the bacpypes helper (broadcast, or directed to `target`
    for another subnet). The bacnet-stack CLI is MS/TP-only, so IP goes here."""
    res = run_ip_helper(["whois"] + ([target] if target else []), timeout=14)
    if res is not None:
        res.setdefault("datalink", "bip")
        res.setdefault("target", target)
        return res
    return {"devices": [], "datalink": "bip", "target": target,
            "note": "BACnet/IP helper unavailable", "ts": time.time()}


def get_discover_all():
    """Plug-and-play: sweep EVERY serial port (MS/TP baud sweep) AND every network
    interface (BACnet/IP Who-Is), then aggregate every device found across all
    links. Each device is tagged with how to reach it (datalink/iface/baud)."""
    tool = os.path.join(BACNET_BIN, "bacwi")
    if not os.path.exists(tool):
        return {"connections": [], "devices": [], "note": "bacnet-stack tools not installed", "ts": time.time()}
    caps = get_capabilities()
    connections = []
    devices = []
    # MS/TP: each serial port, sweep bauds, stop at the first that answers.
    for port in caps["serial_ports"]:
        found_baud, devs = None, []
        for b in SWEEP_BAUDS:
            _, so, _ = run([tool], timeout=10, env=bacnet_env("mstp", b, iface=port))
            d = _parse_devices(so)
            if d:
                found_baud, devs = b, d
                break
        connections.append({"transport": "mstp", "where": port, "baud": found_baud, "count": len(devs)})
        for i in devs:
            devices.append({"instance": i, "datalink": "mstp", "iface": port, "baud": found_baud})
    # BACnet/IP: one broadcast Who-Is via the bacpypes helper (covers the subnet).
    ip = run_ip_helper(["whois"], timeout=12) or {}
    ip_devs = ip.get("devices", [])
    connections.append({"transport": "bip", "where": "LAN broadcast", "count": len(ip_devs)})
    for d in ip_devs:
        devices.append({"instance": d.get("instance"), "datalink": "bip", "iface": None})
    note = "" if devices else "scanned all links; no devices answered yet"
    return {"connections": connections, "devices": devices, "count": len(devices), "note": note, "ts": time.time()}


def get_points(device, datalink="mstp", iface=None):
    tool = os.path.join(BACNET_BIN, "bacrp")
    if not os.path.exists(tool):
        return {"points": [], "note": "bacnet-stack tools not installed", "ts": time.time()}
    if not device:
        return {"points": [], "note": "no device specified", "ts": time.time()}
    # Best-effort: read a few common objects (present-value = property 85).
    probes = [
        ("analog-input", 0, "analog-input:0"),
        ("binary-input", 1, "binary-input:1"),
        ("analog-value", 2, "analog-value:2"),
    ]
    points = []
    type_map = {"analog-input": 0, "binary-input": 3, "analog-value": 2}
    for tname, inst, label in probes:
        rc, so, _ = run([tool, str(device), str(type_map[tname]), str(inst), "85"], timeout=6, env=bacnet_env(datalink, iface=iface))
        points.append(
            {"object": label, "value": so.strip() if rc == 0 and so else None, "ok": rc == 0}
        )
    return {"points": points, "device": device, "ts": time.time()}


def get_points_map():
    mapping = {}
    try:
        if os.path.exists(POINTS_FILE):
            with open(POINTS_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                mapping = {str(k): str(v) for k, v in data.items()}
    except Exception as e:  # noqa: BLE001
        return {"mapping": {}, "note": f"read error: {e}", "file": POINTS_FILE, "ts": time.time()}
    return {"mapping": mapping, "known": KNOWN_POINTS, "file": POINTS_FILE, "ts": time.time()}


def save_points_map(data):
    if not isinstance(data, dict):
        return {"ok": False, "error": "body must be a JSON object of name -> 'objtype:instance'"}
    clean = {}
    for k, v in data.items():
        name = str(k).strip()
        val = str(v).strip()
        if not name or not val:
            continue
        if not _OBJ_RE.match(val):
            return {"ok": False, "error": f"'{name}': '{val}' must look like 'analog-input:2'"}
        clean[name] = val
    try:
        tmp = POINTS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(clean, f, indent=2)
        os.replace(tmp, POINTS_FILE)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    return {"ok": True, "mapping": clean, "count": len(clean), "file": POINTS_FILE, "ts": time.time()}


POLLER = os.path.expanduser("~/pams_ip_poller.py")


def start_monitor(body):
    """Auto-start a BACnet/IP poller for a device so it streams to the dashboard.
    Snapshots the saved point map per-unit, (re)launches the poller (no sudo),
    and persists it across reboots via the user crontab."""
    if not isinstance(body, dict):
        return {"ok": False, "error": "expected JSON object"}
    try:
        device = int(body.get("device"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "valid device instance required"}
    datalink = body.get("datalink", "bip")
    if datalink != "bip":
        return {"ok": False, "error": "auto-monitor currently supports BACnet/IP"}
    unit = re.sub(r"[^0-9A-Za-z_-]+", "-", str(body.get("unit_id") or "").strip()).strip("-")
    if not unit:
        unit = f"DEV-{device}"
    if not (os.path.exists(VENV_PY) and os.path.exists(POLLER)):
        return {"ok": False, "error": "poller/venv not installed on the Pi"}
    # Per-unit point file so multiple devices don't collide.
    unit_pts = os.path.expanduser(f"~/pams_points_{unit}.json")
    try:
        src = POINTS_FILE if os.path.exists(POINTS_FILE) else None
        if src:
            with open(src) as f:
                data = f.read()
            with open(unit_pts, "w") as f:
                f.write(data)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"map snapshot failed: {e}"}
    # Stop any existing poller for this unit, start a fresh one.
    run(["pkill", "-f", f"pams_ip_poller.py {unit} "], timeout=5)
    env = os.environ.copy()
    env["PAMS_POINTS_FILE"] = unit_pts
    log = os.path.expanduser(f"~/pams_poller_{unit}.log")
    try:
        with open(log, "ab") as lf:
            subprocess.Popen(["setsid", VENV_PY, "-u", POLLER, unit, str(device)],
                             env=env, stdout=lf, stderr=lf, stdin=subprocess.DEVNULL,
                             start_new_session=True)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    # Persist across reboots (user crontab).
    try:
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout or ""
        kept = [ln for ln in cur.splitlines()
                if not re.search(rf"pams_ip_poller\.py {re.escape(unit)}\b", ln)]
        kept.append(f"@reboot PAMS_POINTS_FILE={unit_pts} setsid {VENV_PY} -u {POLLER} "
                    f"{unit} {device} >{log} 2>&1")
        subprocess.run(["crontab", "-"], input="\n".join(kept) + "\n", text=True, timeout=5)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "unit": unit, "device": device, "ts": time.time()}


# BACnet object types we scan, mapped to short (ICC-style) codes.
OBJ_TYPES = {
    "analog-input": "AI", "analog-output": "AO", "analog-value": "AV",
    "binary-input": "BI", "binary-output": "BO", "binary-value": "BV",
    "multi-state-input": "MSI", "multi-state-output": "MSO", "multi-state-value": "MSV",
}
_OBJLIST_RE = re.compile(
    r"(" + "|".join(OBJ_TYPES) + r")[\s,:()]+(\d+)"
)


def suggest_channel(object_name):
    """Turn a BACnet object name into a safe snake_case channel key (real name,
    nothing invented). e.g. 'C1SuctionTemperature' -> 'c1_suction_temperature'."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", object_name or "")
    s = re.sub(r"[^0-9A-Za-z]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_").lower()
    return s or "point"


def get_scan(device, limit=250, datalink="mstp", iface=None, baud=None, target=None):
    """YABE-style: read a device's object-list, then each object's name + value.
    BACnet/IP uses the bacpypes helper; MS/TP uses the bacnet-stack CLI."""
    if not device:
        return {"objects": [], "note": "no device specified", "ts": time.time()}
    if datalink == "bip":
        res = run_ip_helper(["scan", device] + ([target] if target else []), timeout=45)
        if res is not None:
            return res
        return {"objects": [], "device": device, "note": "BACnet/IP helper unavailable", "ts": time.time()}
    tool = os.path.join(BACNET_BIN, "bacrp")
    if not os.path.exists(tool):
        return {"objects": [], "note": "bacnet-stack tools not installed", "ts": time.time()}
    scan_env = bacnet_env(datalink, baud, iface)
    mac = ["--mac", target] if (datalink == "bip" and target) else []
    # Property 76 = object-list on the device object.
    rc, so, se = run([tool, str(device), "device", str(device), "76"] + mac, timeout=20, env=scan_env)
    if rc != 0 or not so:
        note = "scan timed out" if rc == 124 else (se or "no object-list returned (no hardware?)")
        return {"objects": [], "device": device, "note": note, "ts": time.time()}
    seen = []
    objects = []
    for m in _OBJLIST_RE.finditer(so):
        tname, inst = m.group(1), int(m.group(2))
        key = (tname, inst)
        if key in seen:
            continue
        seen.append(key)
        if len(objects) >= limit:
            break
        _, nm, _ = run([tool, str(device), tname, str(inst), "77"] + mac, timeout=6, env=scan_env)   # object-name
        _, pv, _ = run([tool, str(device), tname, str(inst), "85"] + mac, timeout=6, env=scan_env)   # present-value
        units = None
        if tname.startswith("analog"):
            _, un, _ = run([tool, str(device), tname, str(inst), "117"] + mac, timeout=6, env=scan_env)  # units
            units = (un or "").strip().strip('"') or None
        name = (nm or "").strip().strip('"') or f"{OBJ_TYPES[tname]}{inst}"
        objects.append({
            "type": tname,
            "short": OBJ_TYPES[tname],
            "instance": inst,
            "object": f"{tname}:{inst}",
            "name": name,
            "value": (pv or "").strip() or None,
            "units": units,
            "suggest": suggest_channel(name),
        })
    note = "" if objects else "object-list parsed but no readable objects"
    return {"objects": objects, "device": device, "count": len(objects), "note": note, "ts": time.time()}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/api/health":
                self._send(200, {"ok": True, "ts": time.time()})
            elif u.path == "/api/services":
                self._send(200, cached("services", 4, get_services))
            elif u.path == "/api/capabilities":
                self._send(200, cached("capabilities", 10, get_capabilities))
            elif u.path == "/api/devices":
                dl = (q.get("datalink") or ["mstp"])[0]
                itf = (q.get("iface") or [""])[0] or None
                self._send(200, cached(f"devices:{dl}:{itf}", 30, lambda: get_devices(dl, itf)))
            elif u.path == "/api/points":
                dev = (q.get("device") or [""])[0]
                dl = (q.get("datalink") or ["mstp"])[0]
                itf = (q.get("iface") or [""])[0] or None
                self._send(200, get_points(dev, dl, itf))
            elif u.path == "/api/scan":
                dev = (q.get("device") or [""])[0]
                dl = (q.get("datalink") or ["mstp"])[0]
                itf = (q.get("iface") or [""])[0] or None
                bd = (q.get("baud") or [""])[0] or None
                tgt = (q.get("target") or [""])[0] or None
                self._send(200, cached(f"scan:{dl}:{itf}:{bd}:{tgt}:{dev}", 20,
                                       lambda: get_scan(dev, datalink=dl, iface=itf, baud=bd, target=tgt)))
            elif u.path == "/api/bus-scan":
                self._send(200, cached("bus-scan", 15, get_bus_scan))
            elif u.path == "/api/ip-scan":
                tgt = (q.get("target") or [""])[0] or None
                self._send(200, cached(f"ip-scan:{tgt}", 15, lambda: get_ip_scan(tgt)))
            elif u.path == "/api/discover-all":
                self._send(200, cached("discover-all", 15, get_discover_all))
            elif u.path == "/api/points-map":
                self._send(200, get_points_map())
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": str(e)})

    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        try:
            if u.path == "/api/points-map":
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else {}
                except json.JSONDecodeError as e:
                    self._send(400, {"ok": False, "error": f"invalid JSON: {e}"})
                    return
                result = save_points_map(body)
                self._send(200 if result.get("ok") else 400, result)
            elif u.path == "/api/monitor":
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else {}
                except json.JSONDecodeError as e:
                    self._send(400, {"ok": False, "error": f"invalid JSON: {e}"})
                    return
                result = start_monitor(body)
                self._send(200 if result.get("ok") else 400, result)
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": str(e)})

    def log_message(self, *_args):  # quiet
        pass


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"PAMS gateway listening on :{PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
