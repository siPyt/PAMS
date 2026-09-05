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

import paho.mqtt.client as mqtt

from pams_ml import PamsML, ACTIVE_MODELS


MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
IN_TOPIC = os.environ.get("IN_TOPIC", "pams/freezers/+")
OUT_PREFIX = os.environ.get("OUT_PREFIX", "pams/scored")

engine = PamsML()


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
    temp = data.get("temperature")
    if temp is None:
        return
    ts = data.get("ts")
    door = int(data.get("door_status", 0))

    ml = engine.score(unit_id, float(temp), door_status=door, ts=ts)

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
        "ts": ts if ts is not None else None,
    }

    client.publish(f"{OUT_PREFIX}/{unit_id}", json.dumps(enriched), qos=0)

    tag = "TRAIN" if ml["training"] else ("ANOM" if ml["anomaly"] else "ok")
    extra = f" if={ml['if_health']}"
    if ml["hmm_health"] is not None:
        extra += f" hmm={ml['hmm_health']}"
    if ml["lstm_health"] is not None:
        extra += f" lstm={ml['lstm_health']}"
    if ml["rul_days"] is not None:
        extra += f" rul={ml['rul_days']}d"
    print(f"{unit_id}: temp={round(float(temp),2)}C  health={ml['health_score']} ens={ml['ensemble_health']}{extra}  "
          f"[{tag}]  ({ml['n_points']}/{ml['baseline']})")


def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    print(f"PAMS ML service: {IN_TOPIC} -> {OUT_PREFIX}/<unit>")
    print(f"Active models: {', '.join(ACTIVE_MODELS)}")
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\nML service stopped.")
        client.disconnect()


if __name__ == "__main__":
    main()
