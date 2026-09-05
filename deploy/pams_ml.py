"""
PAMS ML engine - per-unit ENSEMBLE of four predictive models.

Each freezer unit gets its own rolling feature history and its own set of models
(IsolationForest, LSTM autoencoder, HMM, XGBoost RUL). Models whose dependency is
not installed are skipped automatically. Health scores from the available models
are fused into a single combined health score; XGBoost additionally provides a
Remaining-Useful-Life (RUL) estimate once labeled failure data is supplied.

Feature vector per reading (extensible as more BMS soft sensors come online):
    [ temperature, thermal_velocity, inferred_state, door_status ]

Config via environment:
  PAMS_ML_DIR           history dir (default ~/pams_ml_data)
  PAMS_ML_BASELINE      min points before scoring (default 60)
  PAMS_ML_MAXROWS       rolling window cap (default 20160)
  PAMS_ML_RETRAIN_EVERY refit IF/HMM after this many new points (default 30)
  PAMS_ML_TIMESTEPS     LSTM sequence length (default 10)
  PAMS_ML_HMM_WINDOW    recent rows scored by HMM (default 20)
  PAMS_ML_XGB_WINDOW    XGBoost lag window (default 5)
"""

import os
import csv
import time
from collections import deque

import numpy as np

from pams_models import (
    CompressorHealthModel, LSTMAutoencoderModel, RobustHMMModel, TemporalXGBoostRUL,
    TF_AVAILABLE, HMM_AVAILABLE, XGB_AVAILABLE,
)

DATA_DIR = os.path.expanduser(os.environ.get("PAMS_ML_DIR", "~/pams_ml_data"))
BASELINE = int(os.environ.get("PAMS_ML_BASELINE", "60"))
MAXROWS = int(os.environ.get("PAMS_ML_MAXROWS", "20160"))
RETRAIN_EVERY = int(os.environ.get("PAMS_ML_RETRAIN_EVERY", "30"))
TIMESTEPS = int(os.environ.get("PAMS_ML_TIMESTEPS", "10"))
HMM_WINDOW = int(os.environ.get("PAMS_ML_HMM_WINDOW", "20"))
XGB_WINDOW = int(os.environ.get("PAMS_ML_XGB_WINDOW", "5"))

FEATURES = ["temperature", "thermal_velocity", "inferred_state", "door_status"]
NFEAT = len(FEATURES)
_HEADER = ["timestamp"] + FEATURES

ACTIVE_MODELS = ["isolation_forest"]
if TF_AVAILABLE:
    ACTIVE_MODELS.append("lstm_autoencoder")
if HMM_AVAILABLE:
    ACTIVE_MODELS.append("hmm")
if XGB_AVAILABLE:
    ACTIVE_MODELS.append("xgboost_rul")


class UnitEnsemble:
    """Rolling history + ensemble of models for a single freezer unit."""

    def __init__(self, unit_id):
        self.unit_id = unit_id
        self.path = os.path.join(DATA_DIR, f"{unit_id}.csv")
        self.rows = deque(maxlen=MAXROWS)
        self.last_temp = None
        self.last_time = None
        self.points_since_fit = 0
        self.trained = False
        self.lstm_trained = False

        self.if_model = CompressorHealthModel()
        self.hmm_model = RobustHMMModel() if HMM_AVAILABLE else None
        self.lstm_model = LSTMAutoencoderModel(timesteps=TIMESTEPS, features=NFEAT) if TF_AVAILABLE else None
        self.xgb_model = TemporalXGBoostRUL(window_size=XGB_WINDOW) if XGB_AVAILABLE else None

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
                r = csv.reader(f)
                next(r, None)
                for row in r:
                    if len(row) >= 1 + NFEAT:
                        vals = [float(x) for x in row[1:1 + NFEAT]]
                        self.rows.append(vals)
                        self.last_temp = vals[0]
        except Exception as e:
            print(f"[{self.unit_id}] history load warning: {e}")

    def _append_csv(self, ts, feat):
        try:
            with open(self.path, "a", newline="") as f:
                csv.writer(f).writerow([ts] + list(feat))
        except Exception as e:
            print(f"[{self.unit_id}] history write warning: {e}")

    # -- feature engineering ----------------------------------------------
    def _features(self, temperature, door_status, ts):
        if self.last_temp is None or self.last_time is None:
            velocity = 0.0
        else:
            dt = ts - self.last_time
            velocity = (temperature - self.last_temp) / dt if dt > 0 else 0.0
        inferred = 1 if velocity < 0 else 0
        self.last_temp = temperature
        self.last_time = ts
        return [float(temperature), round(velocity, 4), inferred, int(door_status)]

    # -- main --------------------------------------------------------------
    def process(self, temperature, door_status=0, ts=None):
        ts = ts if ts is not None else time.time()
        feat = self._features(temperature, door_status, ts)
        self.rows.append(feat)
        self._append_csv(ts, feat)
        self.points_since_fit += 1
        n = len(self.rows)

        result = {
            "thermal_velocity": feat[1],
            "inferred_state": feat[2],
            "n_points": n,
            "baseline": BASELINE,
            "training": n < BASELINE,
            "anomaly": 0,
            "health_score": 100.0,
            "ensemble_health": 100.0,
            "if_health": 100.0,
            "hmm_health": None,
            "lstm_health": None,
            "rul_days": None,
            "models": ACTIVE_MODELS,
        }
        if n < BASELINE:
            return result

        data = np.asarray(self.rows, dtype=float)

        # ---- train / retrain -------------------------------------------
        try:
            if (not self.trained) or (self.points_since_fit >= RETRAIN_EVERY):
                self.if_model.train_baseline(data)
                if self.hmm_model is not None:
                    try:
                        self.hmm_model.train_baseline(data)
                    except Exception as e:
                        print(f"[{self.unit_id}] HMM train skipped: {e}")
                self.points_since_fit = 0
                self.trained = True
            # LSTM trained once (expensive); needs >= TIMESTEPS rows
            if self.lstm_model is not None and not self.lstm_trained and n >= TIMESTEPS:
                try:
                    seqs = np.stack([data[i:i + TIMESTEPS] for i in range(len(data) - TIMESTEPS + 1)])
                    self.lstm_model.train_baseline(seqs)
                    self.lstm_trained = True
                    print(f"[{self.unit_id}] LSTM autoencoder trained ({len(seqs)} sequences)")
                except Exception as e:
                    print(f"[{self.unit_id}] LSTM train skipped: {e}")
        except Exception as e:
            print(f"[{self.unit_id}] train error: {e}")
            return result

        # ---- score ------------------------------------------------------
        scores = []
        try:
            if_h = self.if_model.predict_health_score(feat)
            result["if_health"] = if_h
            result["anomaly"] = self.if_model.is_anomaly(feat)
            scores.append(if_h)
        except Exception as e:
            print(f"[{self.unit_id}] IF score error: {e}")

        if self.hmm_model is not None and self.hmm_model.is_trained:
            try:
                hmm_h = self.hmm_model.predict_health_score(list(self.rows)[-HMM_WINDOW:])
                result["hmm_health"] = hmm_h
                scores.append(hmm_h)
            except Exception as e:
                print(f"[{self.unit_id}] HMM score error: {e}")

        if self.lstm_model is not None and self.lstm_trained and n >= TIMESTEPS:
            try:
                lstm_h = self.lstm_model.predict_health_score(
                    np.asarray(list(self.rows)[-TIMESTEPS:], dtype=float))
                result["lstm_health"] = lstm_h
                scores.append(lstm_h)
            except Exception as e:
                print(f"[{self.unit_id}] LSTM score error: {e}")

        # XGBoost RUL requires labeled failure data (train_supervised); stays
        # None until such labels exist. Structurally wired for the future.
        if self.xgb_model is not None and self.xgb_model.is_trained:
            try:
                import pandas as pd
                df = pd.DataFrame(list(self.rows)[-max(XGB_WINDOW * 4, 20):], columns=FEATURES)
                result["rul_days"] = self.xgb_model.predict_remaining_useful_life(df)
            except Exception as e:
                print(f"[{self.unit_id}] XGB score error: {e}")

        # Headline health = IsolationForest (stable, always-available primary).
        # ensemble_health = mean of all available models (fused diagnostic view).
        result["health_score"] = result["if_health"]
        result["ensemble_health"] = round(float(np.mean(scores)), 2) if scores else result["if_health"]

        return result


class PamsML:
    """Manages one UnitEnsemble per unit_id."""

    def __init__(self):
        self.units = {}

    def score(self, unit_id, temperature, door_status=0, ts=None):
        um = self.units.get(unit_id)
        if um is None:
            um = UnitEnsemble(unit_id)
            self.units[unit_id] = um
        return um.process(temperature, door_status=door_status, ts=ts)
