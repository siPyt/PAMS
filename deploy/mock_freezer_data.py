import time
import random
import os
import json
import paho.mqtt.client as mqtt

# --- MQTT Connection Settings ---
# The broker is exposed on the Pi host at localhost:1883 (field-mqtt container).
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
TOPIC_BASE = "pams/freezers"

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.connect(MQTT_HOST, MQTT_PORT, 60)
client.loop_start()

# Initialize 30 Freezer Units
units = {}
for i in range(1, 31):
    uid = f"FRZ-{i:03d}"
    units[uid] = {
        "temp": -20.0 + random.uniform(-0.5, 0.5),
        "door_open": False,
        "door_timer": 0
    }

tick = 0
cascade_tick = 0  # Tracks the sequential domino effect
print(f"Starting PAMS 30-Freezer Simulation -> MQTT {MQTT_HOST}:{MQTT_PORT} ...")

try:
    while True:
        tick += 1

        # Check if override files exist
        force_healthy = os.path.exists(os.path.expanduser("~/force_healthy"))
        cascade_fail = os.path.exists(os.path.expanduser("~/cascade_fail"))

        # Increment cascade timer only if actively cascading and not blocked
        if cascade_fail and not force_healthy:
            cascade_tick += 1
        else:
            cascade_tick = 0

        for uid, state in units.items():
            unit_num = int(uid.split('-')[1])  # Extracts just the number (1-30)
            oscillation = random.uniform(-0.15, 0.15)

            if force_healthy:
                # --- INSTANT RECOVERY MODE ---
                state["door_open"] = False
                if state["temp"] > -20.0:
                    state["temp"] -= 1.0
            elif cascade_fail:
                # --- DOMINO FAILURE MODE ---
                if unit_num <= cascade_tick:
                    state["temp"] += 5.0  # Massive spike to force a 0 score
                else:
                    if state["temp"] > -20.0:
                        state["temp"] -= 0.25
            else:
                # --- STANDARD INJECTED FAILURES ---
                if uid in ["FRZ-003", "FRZ-018"]:
                    state["temp"] += 0.03
                elif uid == "FRZ-012":
                    state["temp"] += random.uniform(0.1, 0.25)
                elif uid in ["FRZ-004", "FRZ-022"]:
                    if tick % 20 == 0:
                        state["door_open"] = True
                        state["door_timer"] = 5
                else:
                    if not state["door_open"] and random.random() < 0.02:
                        state["door_open"] = True
                        state["door_timer"] = random.randint(3, 8)

                # Process door state & normal cooling
                if state["door_open"]:
                    state["temp"] += random.uniform(0.4, 0.8)
                    state["door_timer"] -= 1
                    if state["door_timer"] <= 0:
                        state["door_open"] = False
                else:
                    if uid not in ["FRZ-003", "FRZ-018", "FRZ-012"] and state["temp"] > -20.0:
                        state["temp"] -= 0.25

            # Apply baseline oscillation
            state["temp"] += oscillation

            # --- Calculate Health Score ---
            health_score = 100.0
            if state["temp"] > -18.0:
                health_score -= (state["temp"] + 18.0) * 15
            if state["door_open"]:
                health_score -= 20

            health_score = max(0.0, min(100.0, health_score))

            # Publish this unit's reading as JSON to MQTT
            payload = {
                "unit_id": uid,
                "temperature": float(round(state["temp"], 2)),
                "door_status": 1 if state["door_open"] else 0,
                "health_score": float(round(health_score, 1)),
                "ts": time.time()
            }
            client.publish(f"{TOPIC_BASE}/{uid}", json.dumps(payload), qos=0)

        print(f"Tick {tick}: Published 30 readings to {TOPIC_BASE}/+")
        time.sleep(1)

except KeyboardInterrupt:
    print("\nSimulator stopped.")
finally:
    client.loop_stop()
    client.disconnect()
