#!/usr/bin/env python3
"""
PAMS Gateway — minimal, read-only HTTP API for the Predator app.

Exposes REAL data that Predator's Services / Devices / Points views consume:
  GET  /api/health              -> liveness probe
  GET  /api/services            -> docker containers + systemd unit states
  GET  /api/devices             -> best-effort BACnet Who-Is discovery
  GET  /api/points?device=<id>  -> best-effort BACnet object reads for a device
  GET  /api/scan?device=<id>    -> YABE-style object-list + names + auto-suggest
  GET  /api/points-map          -> current BMS soft-sensor object mapping
  POST /api/points-map          -> save the mapping (~/pams_points.json)

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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("PAMS_GATEWAY_PORT", "8090"))
BACNET_BIN = os.path.expanduser(os.environ.get("PAMS_BACNET_BIN", "~/bacnet-stack/bin"))
POINTS_FILE = os.path.expanduser(os.environ.get("PAMS_POINTS_FILE", "~/pams_points.json"))
SYSTEMD_UNITS = ["pams-ml", "pams-bms"]

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


def run(cmd, timeout=6):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", "not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:  # noqa: BLE001
        return 1, "", str(e)


def cached(key, ttl, producer):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = producer()
    _cache[key] = (now, val)
    return val


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


def get_devices():
    tool = os.path.join(BACNET_BIN, "bacwi")
    if not os.path.exists(tool):
        return {"devices": [], "note": "bacnet-stack tools not installed", "ts": time.time()}
    rc, so, se = run([tool], timeout=5)
    devices = []
    for line in (so or "").splitlines():
        m = re.search(r"(\d{1,7})", line)
        if m and ("device" in line.lower() or "instance" in line.lower()):
            devices.append({"instance": int(m.group(1)), "raw": line.strip()})
    note = "" if devices else "no BACnet devices responded (no hardware on the trunk?)"
    if rc == 124:
        note = "discovery timed out"
    return {"devices": devices, "note": note, "ts": time.time()}


def get_points(device):
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
        rc, so, _ = run([tool, str(device), str(type_map[tname]), str(inst), "85"], timeout=5)
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


def get_scan(device, limit=250):
    """YABE-style: read a device's object-list, then each object's name + value.
    Auto-suggests a PAMS channel per object. Best-effort; needs live hardware."""
    tool = os.path.join(BACNET_BIN, "bacrp")
    if not os.path.exists(tool):
        return {"objects": [], "note": "bacnet-stack tools not installed", "ts": time.time()}
    if not device:
        return {"objects": [], "note": "no device specified", "ts": time.time()}
    # Property 76 = object-list on the device object.
    rc, so, se = run([tool, str(device), "device", str(device), "76"], timeout=20)
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
        _, nm, _ = run([tool, str(device), tname, str(inst), "77"], timeout=6)   # object-name
        _, pv, _ = run([tool, str(device), tname, str(inst), "85"], timeout=6)   # present-value
        name = (nm or "").strip().strip('"') or f"{OBJ_TYPES[tname]}{inst}"
        objects.append({
            "type": tname,
            "short": OBJ_TYPES[tname],
            "instance": inst,
            "object": f"{tname}:{inst}",
            "name": name,
            "value": (pv or "").strip() or None,
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
            elif u.path == "/api/devices":
                self._send(200, cached("devices", 30, get_devices))
            elif u.path == "/api/points":
                dev = (q.get("device") or [""])[0]
                self._send(200, get_points(dev))
            elif u.path == "/api/scan":
                dev = (q.get("device") or [""])[0]
                self._send(200, cached(f"scan:{dev}", 20, lambda: get_scan(dev)))
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
