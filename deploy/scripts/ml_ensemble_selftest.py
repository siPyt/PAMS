"""Self-test for the PAMS ensemble: feed synthetic readings, confirm models score."""
import os
import shutil
import time
import random

os.environ["PAMS_ML_BASELINE"] = "60"
os.environ["PAMS_ML_DIR"] = "/tmp/pams_ens_selftest"
shutil.rmtree("/tmp/pams_ens_selftest", ignore_errors=True)

from pams_ml import PamsML, ACTIVE_MODELS

print("active models:", ACTIVE_MODELS)
eng = PamsML()
base = time.time()
res = None
for i in range(95):
    # steady ~-20C with a warm spike near the end
    temp = -20.0 + random.uniform(-0.25, 0.25) + (6.0 if i == 90 else 0.0)
    door = 1 if i % 40 == 0 else 0
    # real extra BMS soft-sensors (evaporator coil, suction pressure, compressor amps)
    sensors = {
        "evaporator_temp": temp - 4.0 + random.uniform(-0.3, 0.3),
        "suction_pressure": 1.2 + random.uniform(-0.05, 0.05),
        "compressor_current": 4.5 + random.uniform(-0.2, 0.2) + (2.0 if i == 90 else 0.0),
    }
    res = eng.score("FRZ-TEST", temp, door_status=door, ts=base + i * 60, sensors=sensors)

keys = ["health_score", "if_health", "hmm_health", "lstm_health", "rul_days",
        "anomaly", "training", "n_points", "n_features", "channels"]
print("final:", {k: res[k] for k in keys})
assert res is not None and res["n_points"] >= 60, "did not reach baseline"
assert 0.0 <= res["health_score"] <= 100.0, "combined score out of range"
assert res["n_features"] == 2 * len(res["channels"]), "feature/channel mismatch"
assert "compressor_current" in res["channels"], "extra sensor not ingested"
print("ENSEMBLE SELFTEST OK")
