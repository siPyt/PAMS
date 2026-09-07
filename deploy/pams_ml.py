"""
PAMS ML engine - per-unit ENSEMBLE of four predictive models.

Each freezer unit gets its own rolling feature history and its own set of models
(IsolationForest, LSTM autoencoder, HMM, XGBoost RUL). Models whose dependency is
not installed are skipped automatically. Health scores from the available models
are fused into a single combined health score; XGBoost additionally provides a
Remaining-Useful-Life (RUL) estimate once labeled failure data is supplied.

Multi-sensor, per-unit adaptive feature schema: every reading is a dict of REAL
BMS soft-sensor values (whatever the BMS node actually published). A unit's active
channel set grows the first time a new sensor appears, and each active channel
contributes two features - its level and its rate of change (per second):

    feature vector = [ ch0, ch0_rate, ch1, ch1_rate, ... ]

temperature and door_status are the always-present core; any of the optional
BACnet soft-sensors in EXTRA_SENSOR_ORDER are ingested automatically when the BMS
node reports them (evaporator/return/ambient/condenser temp, suction/discharge
pressure, superheat, compressor current, humidity, setpoint, defrost/compressor
status). Nothing is fabricated - absent sensors are simply not in the schema, and
when the schema changes the per-unit models are rebuilt for the new dimensionality.

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
import json
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

# Always-present core channels, then the canonical order of optional real BMS
# soft-sensors. A unit only activates a channel once it actually appears in a
# reading, so single-temperature deployments behave exactly as before.
CORE_CHANNELS = ["temperature", "door_status"]
EXTRA_SENSOR_ORDER = [
    "evaporator_temp", "return_air_temp", "ambient_temp", "condenser_temp",
    "suction_pressure", "discharge_pressure", "superheat",
    "compressor_current", "humidity", "setpoint",
    "defrost_status", "compressor_status",
]
CHANNEL_ORDER = CORE_CHANNELS + EXTRA_SENSOR_ORDER

# Payload keys that are never sensor channels (meta / derived / the raw score).
META_KEYS = {"ts", "unit_id", "health_score", "channels", "models", "n_features", "n_points"}

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
        self.path = os.path.join(DATA_DIR, f"{unit_id}.jsonl")
        self.legacy_csv = os.path.join(DATA_DIR, f"{unit_id}.csv")
        self.raw = deque(maxlen=MAXROWS)     # dicts: {"ts":.., "temperature":.., ...}
        self.rows = deque(maxlen=MAXROWS)    # feature vectors (list[float])
        self.channels = ["temperature"]     # active channels, grows as sensors appear
        self.last_temp = None
        self.points_since_fit = 0
        self.trained = False
        self.lstm_trained = False

        self.if_model = None
        self.hmm_model = None
        self.lstm_model = None
        self.xgb_model = None
        self._build_models()

        os.makedirs(DATA_DIR, exist_ok=True)
        self._load_history()

    # -- feature schema ----------------------------------------------------
    def _nfeat(self):
        return 2 * len(self.channels)

    def _feature_names(self):
        names = []
        for ch in self.channels:
            names.append(ch)
            names.append(f"{ch}_rate")
        return names

    def _build_models(self):
        self.if_model = CompressorHealthModel()
        self.hmm_model = RobustHMMModel() if HMM_AVAILABLE else None
        self.lstm_model = (
            LSTMAutoencoderModel(timesteps=TIMESTEPS, features=self._nfeat())
            if TF_AVAILABLE else None
        )
        self.xgb_model = TemporalXGBoostRUL(window_size=XGB_WINDOW) if XGB_AVAILABLE else None
        self.trained = False
        self.lstm_trained = False
        self.points_since_fit = 0

    def _update_channels(self, reading):
        """Activate any newly-seen numeric channel; returns True if schema changed.

        Any numeric field the BMS publishes becomes a channel (known names sort
        first in canonical order, discovered ones keep first-seen order), so a
        chiller with dozens of real objects is ingested with its real names.
        """
        changed = False
        for ch, v in reading.items():
            if ch in META_KEYS or ch in self.channels or v is None:
                continue
            try:
                float(v)
            except (TypeError, ValueError):
                continue
            self.channels.append(ch)
            changed = True
        if changed:
            known = [c for c in CHANNEL_ORDER if c in self.channels]
            extra = [c for c in self.channels if c not in CHANNEL_ORDER]
            self.channels = known + extra
        return changed

    def _vector(self, r, prev):
        """Build a feature vector (level + per-second rate for each active channel)."""
        vec = []
        for ch in self.channels:
            val = float(r.get(ch, 0.0) or 0.0)
            if prev is None:
                rate = 0.0
            else:
                dt = float(r.get("ts", 0.0)) - float(prev.get("ts", 0.0))
                pval = float(prev.get(ch, val) if prev.get(ch) is not None else val)
                rate = (val - pval) / dt if dt > 0 else 0.0
            vec.append(val)
            vec.append(round(rate, 4))
        return vec

    def _rebuild_rows(self):
        self.rows.clear()
        prev = None
        for r in self.raw:
            self.rows.append(self._vector(r, prev))
            prev = r

    # -- persistence -------------------------------------------------------
    def _append_jsonl(self, reading):
        try:
            with open(self.path, "a") as f:
                f.write(json.dumps(reading) + "\n")
        except Exception as e:
            print(f"[{self.unit_id}] history write warning: {e}")

    def _migrate_csv(self):
        """Read a legacy 4-column CSV into raw readings (ts, temperature, door)."""
        out = []
        try:
            with open(self.legacy_csv, newline="") as f:
                r = csv.reader(f)
                next(r, None)
                for row in r:
                    if len(row) < 2:
                        continue
                    try:
                        ts = float(row[0])
                        temp = float(row[1])
                    except ValueError:
                        continue
                    door = 0
                    try:
                        door = int(float(row[-1]))
                    except ValueError:
                        pass
                    out.append({"ts": ts, "temperature": temp, "door_status": door})
        except Exception as e:
            print(f"[{self.unit_id}] legacy CSV migrate warning: {e}")
        return out

    def _load_history(self):
        loaded = []
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            loaded.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                print(f"[{self.unit_id}] history load warning: {e}")
        elif os.path.exists(self.legacy_csv):
            loaded = self._migrate_csv()

        for r in loaded[-MAXROWS:]:
            self._update_channels(r)
            self.raw.append(r)
        if self.raw:
            self.last_temp = self.raw[-1].get("temperature")
            self._build_models()
            self._rebuild_rows()

    # -- main --------------------------------------------------------------
    def process(self, reading):
        """Score one reading dict. `temperature` is required; extras optional."""
        reading = dict(reading)
        reading["ts"] = reading.get("ts") or time.time()
        reading.setdefault("door_status", 0)
        ts = reading["ts"]

        changed = self._update_channels(reading)
        self.raw.append(reading)
        self._append_jsonl(reading)

        if changed:
            # Feature dimensionality changed -> rebuild models + replay history.
            self._build_models()
            self._rebuild_rows()
        else:
            prev = self.raw[-2] if len(self.raw) >= 2 else None
            self.rows.append(self._vector(reading, prev))

        self.points_since_fit += 1
        self.last_temp = reading.get("temperature")
        n = len(self.rows)
        feat = self.rows[-1]

        temp_idx = self.channels.index("temperature")
        thermal_velocity = feat[2 * temp_idx + 1]
        inferred = 1 if thermal_velocity < 0 else 0

        result = {
            "thermal_velocity": thermal_velocity,
            "inferred_state": inferred,
            "channels": list(self.channels),
            "n_features": self._nfeat(),
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
                df = pd.DataFrame(list(self.rows)[-max(XGB_WINDOW * 4, 20):],
                                  columns=self._feature_names())
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

    def score(self, unit_id, temperature, door_status=0, ts=None, sensors=None):
        """Score a reading. `sensors` is an optional dict of extra REAL sensor
        values (any numeric name the BMS mapped); absent ones are simply not
        ingested - nothing is fabricated."""
        um = self.units.get(unit_id)
        if um is None:
            um = UnitEnsemble(unit_id)
            self.units[unit_id] = um

        reading = {
            "temperature": float(temperature),
            "door_status": int(door_status),
            "ts": ts if ts is not None else time.time(),
        }
        if sensors:
            for k, v in sensors.items():
                if k in META_KEYS or v is None:
                    continue
                try:
                    reading[k] = float(v)
                except (TypeError, ValueError):
                    continue
        return um.process(reading)
