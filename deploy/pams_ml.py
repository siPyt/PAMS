"""
PAMS ML engine - per-unit IsolationForest anomaly scoring.

Refactored from the pilot RealTimeCompressorML so it works for many freezers at
once (the original mixed all units into one global CSV / one last_temp). Each
unit gets its own rolling history, scaler, and model. Retraining happens on an
interval instead of every reading, which keeps the Pi light.

Features per reading (same idea as the pilot):
  - temperature
  - thermal_velocity  (dTemp / dt)
  - inferred_state    (1 if cooling i.e. velocity < 0, else 0)

Health score maps the IsolationForest decision_function to 0-100:
  health = clip((raw + OFFSET) / SCALE * 100, 0, 100)

Config via environment:
  PAMS_ML_DIR          history dir (default ~/pams_ml_data)
  PAMS_ML_BASELINE     min points before scoring (default 10080 = 1 wk @ 1/min)
  PAMS_ML_MAXROWS      rolling window cap (default 20160 = 2 wk @ 1/min)
  PAMS_ML_RETRAIN_EVERY  refit after this many new points (default 30)
  PAMS_ML_CONTAMINATION  IsolationForest contamination (default 0.05)
  PAMS_ML_OFFSET / PAMS_ML_SCALE  score mapping (default 0.3 / 0.45)
"""

import os
import csv
import time
from collections import deque

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler


DATA_DIR = os.path.expanduser(os.environ.get("PAMS_ML_DIR", "~/pams_ml_data"))
BASELINE = int(os.environ.get("PAMS_ML_BASELINE", "10080"))
MAXROWS = int(os.environ.get("PAMS_ML_MAXROWS", "20160"))
RETRAIN_EVERY = int(os.environ.get("PAMS_ML_RETRAIN_EVERY", "30"))
CONTAMINATION = float(os.environ.get("PAMS_ML_CONTAMINATION", "0.05"))
SCORE_OFFSET = float(os.environ.get("PAMS_ML_OFFSET", "0.3"))
SCORE_SCALE = float(os.environ.get("PAMS_ML_SCALE", "0.45"))

_HEADER = ["timestamp", "temperature", "thermal_velocity", "inferred_state"]


class UnitModel:
    """Anomaly model + rolling history for a single freezer unit."""

    def __init__(self, unit_id):
        self.unit_id = unit_id
        self.path = os.path.join(DATA_DIR, f"{unit_id}.csv")
        self.rows = deque(maxlen=MAXROWS)  # each row: (temp, velocity, inferred)
        self.last_temp = None
        self.last_time = None
        self.scaler = StandardScaler()
        self.model = IsolationForest(
            n_estimators=100, contamination=CONTAMINATION, random_state=42
        )
        self.is_fitted = False
        self.points_since_fit = 0
        os.makedirs(DATA_DIR, exist_ok=True)
        self._load_history()

    # -- persistence -------------------------------------------------------
    def _load_history(self):
        if not os.path.exists(self.path):
            with open(self.path, "w", newline="") as f:
                csv.writer(f).writerow(_HEADER)
            return
        try:
            with open(self.path, newline="") as f:
                reader = csv.reader(f)
                next(reader, None)  # header
                for r in reader:
                    if len(r) >= 4:
                        self.rows.append((float(r[1]), float(r[2]), int(float(r[3]))))
                        self.last_temp = float(r[1])
        except Exception as e:
            print(f"[{self.unit_id}] history load warning: {e}")

    def _append_csv(self, ts, temp, velocity, inferred):
        try:
            with open(self.path, "a", newline="") as f:
                csv.writer(f).writerow([ts, temp, velocity, inferred])
        except Exception as e:
            print(f"[{self.unit_id}] history write warning: {e}")

    # -- scoring -----------------------------------------------------------
    def process(self, temperature, ts=None):
        """Ingest one reading, return an enriched result dict."""
        ts = ts if ts is not None else time.time()

        if self.last_temp is None or self.last_time is None:
            velocity = 0.0
        else:
            dt = ts - self.last_time
            velocity = (temperature - self.last_temp) / dt if dt > 0 else 0.0
        inferred = 1 if velocity < 0 else 0

        self.last_temp = temperature
        self.last_time = ts

        self.rows.append((temperature, velocity, inferred))
        self._append_csv(ts, temperature, velocity, inferred)
        self.points_since_fit += 1

        n = len(self.rows)
        result = {
            "thermal_velocity": round(velocity, 4),
            "inferred_state": inferred,
            "n_points": n,
            "baseline": BASELINE,
            "training": n < BASELINE,
            "anomaly": 0,
            "health_score": 100.0,
        }

        # Still gathering baseline -> assume healthy.
        if n < BASELINE:
            return result

        try:
            data = np.asarray(self.rows, dtype=float)
            if (not self.is_fitted) or (self.points_since_fit >= RETRAIN_EVERY):
                scaled = self.scaler.fit_transform(data)
                self.model.fit(scaled)
                self.is_fitted = True
                self.points_since_fit = 0

            live = self.scaler.transform([[temperature, velocity, inferred]])
            raw = float(self.model.decision_function(live)[0])
            is_anom = int(self.model.predict(live)[0] == -1)
            health = float(np.clip((raw + SCORE_OFFSET) / SCORE_SCALE * 100.0, 0, 100))

            result["health_score"] = round(health, 2)
            result["anomaly"] = is_anom
            result["raw_score"] = round(raw, 4)
        except Exception as e:
            print(f"[{self.unit_id}] ML processing error: {e}")

        return result


class PamsML:
    """Manages one UnitModel per unit_id."""

    def __init__(self):
        self.units = {}

    def score(self, unit_id, temperature, ts=None):
        um = self.units.get(unit_id)
        if um is None:
            um = UnitModel(unit_id)
            self.units[unit_id] = um
        return um.process(temperature, ts=ts)
