#!/usr/bin/env python3
"""
PAMS Gateway — minimal, read-only HTTP API for the Predator app.

Exposes REAL data that Predator's Services / Devices / Points views consume:
  GET /api/health              -> liveness probe
  GET /api/services            -> docker containers + systemd unit states
  GET /api/devices             -> best-effort BACnet Who-Is discovery
  GET /api/points?device=<id>  -> best-effort BACnet object reads for a device

Runs as the normal user (no root needed): it only reads `docker`/`systemctl`
status and runs the bacnet-stack CLI tools. Stdlib only — no pip installs.

Start:   python3 ~/pams_gateway.py         (listens on :8090)
Env:     PAMS_GATEWAY_PORT (default 8090)
         PAMS_BACNET_BIN   (default ~/bacnet-stack/bin)
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
SYSTEMD_UNITS = ["pams-ml", "pams-bms"]

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


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
