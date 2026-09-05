#!/usr/bin/env bash
# Attempt to install TensorFlow (for the LSTM autoencoder model). Large download.
# The ensemble runs fine without it - LSTM is skipped gracefully if this fails.
set -u
PIP=~/pams_env/bin/pip
echo '=== attempting tensorflow ==='
$PIP install tensorflow 2>&1 | tail -n 10 \
  || $PIP install tensorflow-aarch64 2>&1 | tail -n 10 \
  || echo 'tensorflow install FAILED (LSTM will be skipped)'
echo
echo '=== verify ==='
~/pams_env/bin/python - <<'PY'
try:
    import tensorflow as tf
    print("tensorflow OK", tf.__version__)
except Exception as e:
    print("tensorflow MISSING:", type(e).__name__)
PY
echo '=== DONE ==='
