#!/usr/bin/env python3
"""PAMS power/health watchdog.

Runs continuously (launched at boot via the user crontab, no sudo). It exists
to answer one question reliably: *is this Pi losing power, and when?*

Because the Pi 5 here has no RTC coin cell, a full power loss makes the clock
jump backwards at the next boot. A normal software reboot does not. This
watchdog turns that behaviour into hard evidence:

- On startup it writes a `boot` record with wall-clock + monotonic uptime and
  detects an *unclean* previous shutdown (a heartbeat that stopped without a
  matching `shutdown` record => power was cut).
- Every INTERVAL seconds it samples `vcgencmd get_throttled` + temperature and
  refreshes a heartbeat file. Undervoltage / throttle transitions are logged as
  events so a brownout at a site is captured even if the Pi survives it.

Everything is append-only JSONL at ~/pams_health.jsonl (stdlib only). The
gateway serves the tail at /api/power so Predator can surface it.
"""

import json
import os
import subprocess
import time

HOME = os.path.expanduser("~")
LOG = os.environ.get("PAMS_HEALTH_LOG", os.path.join(HOME, "pams_health.jsonl"))
HB = os.environ.get("PAMS_HEALTH_HB", os.path.join(HOME, "pams_watchdog_hb.json"))
INTERVAL = int(os.environ.get("PAMS_HEALTH_INTERVAL", "30"))
# If the previous heartbeat is older than this many seconds when we boot, the
# gap was an outage (the watchdog wasn't running / the Pi was off).
STALE_GAP = int(os.environ.get("PAMS_HEALTH_STALE_GAP", str(INTERVAL * 4)))

# Bits in `vcgencmd get_throttled`. "now" = active this instant; "occurred" =
# has happened since boot (latched).
THROTTLE_BITS = {
    0: "undervoltage_now",
    1: "arm_freq_capped_now",
    2: "throttled_now",
    3: "soft_temp_limit_now",
    16: "undervoltage_occurred",
    17: "arm_freq_capped_occurred",
    18: "throttled_occurred",
    19: "soft_temp_limit_occurred",
}


def _run(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def monotonic_uptime():
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except Exception:  # noqa: BLE001
        return 0.0


def throttled_raw():
    out = _run(["vcgencmd", "get_throttled"])  # e.g. "throttled=0x50000"
    try:
        return int(out.split("=")[1], 16)
    except Exception:  # noqa: BLE001
        return None


def decode_throttled(raw):
    if raw is None:
        return {"raw": None, "flags": []}
    flags = [name for bit, name in THROTTLE_BITS.items() if raw & (1 << bit)]
    return {"raw": hex(raw), "flags": flags}


def temp_c():
    out = _run(["vcgencmd", "measure_temp"])  # e.g. "temp=41.2'C"
    try:
        return float(out.split("=")[1].split("'")[0])
    except Exception:  # noqa: BLE001
        return None


def append(rec):
    rec["ts"] = time.time()
    rec["uptime_s"] = round(monotonic_uptime(), 1)
    try:
        with open(LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001
        pass


def write_hb(extra=None):
    rec = {
        "ts": time.time(),
        "uptime_s": round(monotonic_uptime(), 1),
        "throttled": decode_throttled(throttled_raw()),
        "temp_c": temp_c(),
    }
    if extra:
        rec.update(extra)
    tmp = HB + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(rec, f)
        os.replace(tmp, HB)
    except Exception:  # noqa: BLE001
        pass
    return rec


def read_prev_hb():
    try:
        with open(HB) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def boot_record():
    """Write a boot marker and classify the previous shutdown."""
    prev = read_prev_hb()
    now = time.time()
    unclean = False
    gap = None
    if prev and isinstance(prev.get("ts"), (int, float)):
        gap = round(now - prev["ts"], 1)
        # Heartbeat stopped without a clean 'shutdown' marker and we've just
        # booted (small uptime) => the previous run died with the power.
        if gap > STALE_GAP and monotonic_uptime() < STALE_GAP:
            unclean = True
    append({
        "event": "boot",
        "unclean_previous_shutdown": unclean,
        "gap_since_last_heartbeat_s": gap,
        "throttled": decode_throttled(throttled_raw()),
        "temp_c": temp_c(),
        "model": _read_model(),
    })


def _read_model():
    try:
        with open("/proc/device-tree/model") as f:
            return f.read().strip("\x00").strip()
    except Exception:  # noqa: BLE001
        return ""


def main():
    boot_record()
    last_flags = set(decode_throttled(throttled_raw())["flags"])
    # Log the initial power state so a run always has a baseline sample.
    append({"event": "sample", **write_hb()})
    while True:
        try:
            time.sleep(INTERVAL)
            hb = write_hb()
            flags = set(hb["throttled"]["flags"])
            # Only log when something changes (a new undervoltage/throttle
            # event) to keep the JSONL small but capture every transition.
            if flags != last_flags:
                gained = sorted(flags - last_flags)
                cleared = sorted(last_flags - flags)
                append({"event": "throttle_change", "gained": gained,
                        "cleared": cleared, **hb})
                last_flags = flags
        except KeyboardInterrupt:
            append({"event": "shutdown", "reason": "signal"})
            break
        except Exception as e:  # noqa: BLE001
            append({"event": "error", "error": str(e)})
            time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
