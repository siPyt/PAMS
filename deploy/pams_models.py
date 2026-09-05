"""
PAMS predictive models - four production-hardened ML models for freezer
degradation, adapted for the PAMS pipeline.

Heavy/optional dependencies (TensorFlow, hmmlearn, XGBoost) are imported lazily,
so the ensemble runs with whatever is installed and simply skips models whose
dependency is missing. IsolationForest (scikit-learn) is always available.

Models:
  1. CompressorHealthModel  - IsolationForest anomaly -> 0-100 health
  2. LSTMAutoencoderModel    - temporal reconstruction error -> 0-100 health (TensorFlow)
  3. RobustHMMModel          - length-normalized HMM sequence health (hmmlearn)
  4. TemporalXGBoostRUL      - supervised Remaining-Useful-Life in days (xgboost)
"""

import os
import warnings
import logging
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

# hmmlearn re-emits "will be overwritten during initialization" warnings on every
# refit, and both libs emit convergence warnings - keep the service logs clean.
warnings.filterwarnings("ignore", message=".*overwritten during initialization.*")
warnings.filterwarnings("ignore", message="Even though the")
warnings.filterwarnings("ignore", category=UserWarning, module="hmmlearn")
logging.getLogger("hmmlearn").setLevel(logging.ERROR)
try:
    from sklearn.exceptions import ConvergenceWarning
    warnings.filterwarnings("ignore", category=ConvergenceWarning)
except Exception:
    pass

# ---- optional dependencies -------------------------------------------------
try:
    from hmmlearn import hmm
    HMM_AVAILABLE = True
except Exception:
    HMM_AVAILABLE = False

try:
    import pandas as pd
    import xgboost as xgb
    XGB_AVAILABLE = True
except Exception:
    XGB_AVAILABLE = False

try:
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.layers import LSTM, RepeatVector, TimeDistributed, Dense
    TF_AVAILABLE = True
except Exception:
    TF_AVAILABLE = False


# ---------------------------------------------------------------------------
# 1. Isolation Forest  (semi-supervised anomaly detection)
# ---------------------------------------------------------------------------
class CompressorHealthModel:
    def __init__(self):
        self.scaler = StandardScaler()
        self.model = IsolationForest(n_estimators=100, contamination=0.05, random_state=42)
        self.is_trained = False

    def train_baseline(self, historical_telemetry):
        scaled = self.scaler.fit_transform(historical_telemetry)
        self.model.fit(scaled)
        self.is_trained = True

    def predict_health_score(self, live_data_point):
        if not self.is_trained:
            return 100.0
        scaled_point = self.scaler.transform([live_data_point])
        raw_score = self.model.decision_function(scaled_point)[0]
        return round(float(np.clip((raw_score + 0.5) / 0.65 * 100, 0, 100)), 2)

    def is_anomaly(self, live_data_point):
        if not self.is_trained:
            return 0
        scaled_point = self.scaler.transform([live_data_point])
        return int(self.model.predict(scaled_point)[0] == -1)


# ---------------------------------------------------------------------------
# 2. LSTM Autoencoder  (deep learning, temporal reconstruction error)
# ---------------------------------------------------------------------------
class LSTMAutoencoderModel:
    def __init__(self, timesteps=10, features=4):
        self.scaler = StandardScaler()
        self.timesteps = timesteps
        self.features = features
        self.is_trained = False
        self.threshold = 0.0
        self.model = Sequential([
            LSTM(16, activation="relu", input_shape=(timesteps, features), return_sequences=False),
            RepeatVector(timesteps),
            LSTM(16, activation="relu", return_sequences=True),
            TimeDistributed(Dense(features)),
        ])
        self.model.compile(optimizer="adam", loss="mse")

    def train_baseline(self, historical_sequences):
        flat = historical_sequences.reshape(-1, self.features)
        scaled_flat = self.scaler.fit_transform(flat)
        scaled_seq = scaled_flat.reshape(-1, self.timesteps, self.features)
        self.model.fit(scaled_seq, scaled_seq, epochs=50, batch_size=32, verbose=0)
        recon = self.model.predict(scaled_seq, verbose=0)
        mse = np.mean(np.power(scaled_seq - recon, 2), axis=(1, 2))
        self.threshold = float(np.percentile(mse, 95)) or 1e-6
        self.is_trained = True

    def predict_health_score(self, live_sequence):
        if not self.is_trained:
            return 100.0
        scaled_seq = self.scaler.transform(live_sequence).reshape(1, self.timesteps, self.features)
        recon = self.model.predict(scaled_seq, verbose=0)
        mse = float(np.mean(np.power(scaled_seq - recon, 2)))
        thr = self.threshold if self.threshold > 0 else 1e-6
        score = 100 - (mse / thr) * 5
        return round(float(np.clip(score, 0, 100)), 2)


# ---------------------------------------------------------------------------
# 3. Length-Normalized Hidden Markov Model
# ---------------------------------------------------------------------------
class RobustHMMModel:
    def __init__(self):
        self.scaler = StandardScaler()
        self.model = hmm.GaussianHMM(n_components=3, covariance_type="full", n_iter=100, min_covar=1e-3)
        self.is_trained = False
        self.baseline_log_prob_per_step = 0.0

    def train_baseline(self, historical_sequence):
        scaled = self.scaler.fit_transform(historical_sequence)
        # Recreate the model each fit so hmmlearn doesn't warn about overwriting
        # already-set parameters on retrain.
        self.model = hmm.GaussianHMM(n_components=3, covariance_type="full",
                                     n_iter=100, min_covar=1e-3)
        self.model.fit(scaled)
        raw_log_prob = self.model.score(scaled)
        self.baseline_log_prob_per_step = raw_log_prob / max(len(scaled), 1)
        self.is_trained = True

    def predict_health_score(self, recent_sequence_buffer):
        if not self.is_trained or len(recent_sequence_buffer) < 5:
            return 100.0
        if self.baseline_log_prob_per_step == 0.0:
            return 100.0
        scaled_seq = self.scaler.transform(recent_sequence_buffer)
        try:
            current = self.model.score(scaled_seq) / max(len(scaled_seq), 1)
        except Exception:
            return 100.0
        ratio = current / self.baseline_log_prob_per_step
        return round(float(np.clip(100 - (abs(1 - ratio) * 100), 0, 100)), 2)


# ---------------------------------------------------------------------------
# 4. XGBoost with automated lag engineering  (supervised RUL in days)
# ---------------------------------------------------------------------------
class TemporalXGBoostRUL:
    def __init__(self, window_size=5):
        self.window_size = window_size
        self.is_trained = False
        self.model = xgb.XGBRegressor(
            n_estimators=200, learning_rate=0.01, max_depth=4, subsample=0.8, random_state=42
        )

    def _engineer_features(self, df):
        eng = df.copy()
        for col in df.columns:
            eng[f"{col}_roll_mean"] = df[col].rolling(self.window_size).mean()
            eng[f"{col}_roll_std"] = df[col].rolling(self.window_size).std()
        return eng.dropna()

    def train_supervised(self, historical_df, target_rul_series):
        feats = self._engineer_features(historical_df)
        targets = target_rul_series.loc[feats.index]
        self.model.fit(feats, targets)
        self.is_trained = True

    def predict_remaining_useful_life(self, live_buffer_df):
        if not self.is_trained or len(live_buffer_df) < self.window_size:
            return None
        feats = self._engineer_features(live_buffer_df)
        if len(feats) == 0:
            return None
        rul = self.model.predict(feats.iloc[[-1]])[0]
        return round(max(0.0, float(rul)), 1)
