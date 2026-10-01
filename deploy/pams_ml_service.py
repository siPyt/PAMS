"""
PAMS ML scoring service.

Subscribes to raw freezer readings on MQTT (pams/freezers/+), runs the per-unit
IsolationForest engine (pams_ml.PamsML), and republishes an ML-enriched record
to pams/scored/<unit>. Node-RED writes that scored stream to InfluxDB (measurement
"ml_scores") for Grafana.

This decouples the ML from the BACnet I/O, so it works for the simulator and the
real BMS node identically - both just publish temperature to pams/freezers/<unit>.

Config via environment:
  MQTT_HOST / MQTT_PORT   broker (default localhost:1883)
  IN_TOPIC                raw subscribe topic (default pams/freezers/+)
  OUT_PREFIX              scored publish prefix (default pams/scored)
  (plus all PAMS_ML_* vars consumed by pams_ml)
"""

import os
import json
import time
import threading

import paho.mqtt.client as mqtt

from pams_ml import PamsML, ACTIVE_MODELS


MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
IN_TOPIC = os.environ.get("IN_TOPIC", "pams/freezers/+")
OUT_PREFIX = os.environ.get("OUT_PREFIX", "pams/scored")

# Dead-man / staleness alerting: if a unit stops publishing (freezer offline,
# poller died, cable pulled) we raise a retained alert on pams/alerts/<unit>.
STALE_AFTER = float(os.environ.get("PAMS_STALE_SECONDS", "60"))
STALE_CHECK = float(os.environ.get("PAMS_STALE_CHECK_SECONDS", "15"))
ALERT_PREFIX = os.environ.get("PAMS_ALERT_PREFIX", "pams/alerts")
LAST_SEEN_FILE = os.path.expanduser(os.environ.get("PAMS_LAST_SEEN_FILE", "~/pams_last_seen.json"))
KNOWN_UNITS_FILE = os.path.expanduser(os.environ.get("PAMS_KNOWN_UNITS_FILE", "~/pams_known_units.json"))
ALERTS_LOG = os.path.expanduser(os.environ.get("PAMS_ALERTS_LOG", "~/pams_alerts.jsonl"))

_last_seen = {}          # unit -> wall-clock ts of its last reading
_known_units = set()     # every unit ever seen (persisted, so a dead unit is still watched)
_alerted = set()         # units currently flagged stale
_lock = threading.Lock()
CLIENT = None

# Fields that are meta or already handled explicitly - everything else numeric is
# treated as a real sensor channel and forwarded to the ML as-is.
NON_SENSOR = {"unit_id", "temperature", "door_status", "health_score", "ts"}
engine = PamsML()


def _atomic_write(path, obj):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        pass


def _save_last_seen():
    with _lock:
        snapshot = dict(_last_seen)
    _atomic_write(LAST_SEEN_FILE, snapshot)


def _load_known_units():
    try:
        with open(KNOWN_UNITS_FILE) as f:
            for u in json.load(f):
                _known_units.add(u)
    except Exception:  # noqa: BLE001
        pass


def _save_known_units():
    with _lock:
        units = sorted(_known_units)
    _atomic_write(KNOWN_UNITS_FILE, units)


def _log_alert(rec):
    try:
        with open(ALERTS_LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _emit_alert(unit, stale, age, last_seen):
    rec = {
        "unit_id": unit,
        "alert": "stale" if stale else "ok",
        "severity": "critical" if stale else "info",
        "age_s": round(age, 1),
        "threshold_s": STALE_AFTER,
        "last_seen": last_seen,
        "ts": time.time(),
    }
    if CLIENT is not None:
        try:
            CLIENT.publish(f"{ALERT_PREFIX}/{unit}", json.dumps(rec), qos=1, retain=True)
        except Exception:  # noqa: BLE001
            pass
    _log_alert(rec)
    print(f"ALERT {unit}: {rec['alert'].upper()} (age={rec['age_s']}s, threshold={STALE_AFTER}s)")


def _stale_monitor():
    """Background loop: flag units whose last reading is older than STALE_AFTER,
    and clear the flag when they resume."""
    while True:
        time.sleep(STALE_CHECK)
        now = time.time()
        with _lock:
            units = list(_known_units)
            seen = dict(_last_seen)
            alerted = set(_alerted)
        for unit in units:
            last = seen.get(unit)
            if last is None:
                continue
            age = now - last
            if age > STALE_AFTER and unit not in alerted:
                with _lock:
                    _alerted.add(unit)
                _emit_alert(unit, True, age, last)
            elif age <= STALE_AFTER and unit in alerted:
                with _lock:
                    _alerted.discard(unit)
                _emit_alert(unit, False, age, last)


def _mark_seen(unit_id):
    now = time.time()
    with _lock:
        _last_seen[unit_id] = now
        new_unit = unit_id not in _known_units
        _known_units.add(unit_id)
        was_alerted = unit_id in _alerted
        _alerted.discard(unit_id)
    _save_last_seen()
    if new_unit:
        _save_known_units()
    if was_alerted:
        _emit_alert(unit_id, False, 0.0, now)


def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"Connected to MQTT {MQTT_HOST}:{MQTT_PORT} (rc={reason_code}); subscribing {IN_TOPIC}")
    client.subscribe(IN_TOPIC, qos=0)


def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode("utf-8"))
    except Exception:
        return

    # Ignore our own scored stream if topics ever overlap.
    if msg.topic.startswith(OUT_PREFIX):
        return

    unit_id = data.get("unit_id") or msg.topic.rsplit("/", 1)[-1]
    # A reading arriving at all means this unit is alive - mark it before anything
    # else so the dead-man monitor and /api/selftest see it as fresh.
    _mark_seen(unit_id)
    temp = data.get("temperature")
    if temp is None:
        return
    ts = data.get("ts")
    door = int(data.get("door_status", 0))

    # Any REAL extra sensor the node published (any numeric name) flows into the ML.
    sensors = {}
    labels = {}
    for k, v in data.items():
        if k in NON_SENSOR or v is None:
            continue
        try:
            sensors[k] = float(v)
        except (TypeError, ValueError):
            if isinstance(v, str):
                labels[k] = v
            continue

    ml = engine.score(unit_id, float(temp), door_status=door, ts=ts, sensors=sensors)

    enriched = {
        "unit_id": unit_id,
        "temperature": float(temp),
        "door_status": door,
        # combined health (fused across available models):
        "health_score": ml["health_score"],
        "ml_health_score": ml["health_score"],
        "ensemble_health": ml["ensemble_health"],
        # per-model health + RUL:
        "if_health": ml["if_health"],
        "hmm_health": ml["hmm_health"],
        "lstm_health": ml["lstm_health"],
        "rul_days": ml["rul_days"],
        "thermal_velocity": ml["thermal_velocity"],
        "inferred_state": ml["inferred_state"],
        "anomaly": ml["anomaly"],
        "training": 1 if ml["training"] else 0,
        "n_points": ml["n_points"],
        # multi-sensor schema the model is actually using this reading:
        "channels": ml["channels"],
        "n_features": ml["n_features"],
        "ts": ts if ts is not None else None,
    }
    # Echo the real extra sensor values so Influx/Grafana can trend them too.
    for k, v in sensors.items():
        enriched[k] = v
    # Pass through non-numeric text points (e.g. character-string values) as labels.
    for k, v in labels.items():
        enriched[k] = v

    client.publish(f"{OUT_PREFIX}/{unit_id}", json.dumps(enriched), qos=0)

    tag = "TRAIN" if ml["training"] else ("ANOM" if ml["anomaly"] else "ok")
    extra = f" if={ml['if_health']}"
    if ml["hmm_health"] is not None:
        extra += f" hmm={ml['hmm_health']}"
    if ml["lstm_health"] is not None:
        extra += f" lstm={ml['lstm_health']}"
    if ml["rul_days"] is not None:
        extra += f" rul={ml['rul_days']}d"
    if sensors:
        extra += f" +{len(sensors)}sensors"
    print(f"{unit_id}: temp={round(float(temp),2)}C  health={ml['health_score']} ens={ml['ensemble_health']}{extra}  "
          f"[{tag}]  ({ml['n_points']}/{ml['baseline']}, {ml['n_features']}feat)")


def main():
    global CLIENT
    _load_known_units()
    # Seed known units with a fresh timestamp so a restart gives them one grace
    # window before being flagged (rather than alerting instantly on boot).
    now = time.time()
    with _lock:
        for u in _known_units:
            _last_seen.setdefault(u, now)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    CLIENT = client
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    threading.Thread(target=_stale_monitor, daemon=True).start()
    print(f"PAMS ML service: {IN_TOPIC} -> {OUT_PREFIX}/<unit>")
    print(f"Dead-man alerts: stale after {STALE_AFTER}s -> {ALERT_PREFIX}/<unit>")
    print(f"Active models: {', '.join(ACTIVE_MODELS)}")
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\nML service stopped.")
        client.disconnect()


if __name__ == "__main__":
    main()
