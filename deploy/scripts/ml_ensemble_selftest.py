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
    res = eng.score("FRZ-TEST", temp, door_status=door, ts=base + i * 60)

keys = ["health_score", "if_health", "hmm_health", "lstm_health", "rul_days",
        "anomaly", "training", "n_points"]
print("final:", {k: res[k] for k in keys})
assert res is not None and res["n_points"] >= 60, "did not reach baseline"
assert 0.0 <= res["health_score"] <= 100.0, "combined score out of range"
print("ENSEMBLE SELFTEST OK")
