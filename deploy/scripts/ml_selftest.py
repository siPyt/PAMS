"""Quick self-test for pams_ml: feed synthetic readings and confirm it scores."""
import os
import shutil
import time
import random

os.environ["PAMS_ML_BASELINE"] = "60"
os.environ["PAMS_ML_DIR"] = "/tmp/pams_ml_selftest"
shutil.rmtree("/tmp/pams_ml_selftest", ignore_errors=True)

from pams_ml import PamsML

eng = PamsML()
base = time.time()
res = None
for i in range(80):
    # steady ~-20C, with a warm spike near the end to exercise anomaly path
    temp = -20.0 + random.uniform(-0.25, 0.25) + (6.0 if i == 78 else 0.0)
    res = eng.score("FRZ-TEST", temp, ts=base + i * 60)

print("final result:", res)
assert res is not None and res["n_points"] >= 60, "did not reach baseline"
assert 0.0 <= res["health_score"] <= 100.0, "score out of range"
print("SELFTEST OK")
