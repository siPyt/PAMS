#!/usr/bin/env bash
# Install lighter ensemble deps (hmmlearn, xgboost) into the PAMS venv.
set -u
PIP=~/pams_env/bin/pip
echo '=== installing hmmlearn + xgboost ==='
$PIP install hmmlearn xgboost 2>&1 | tail -n 8
echo
echo '=== verify ==='
~/pams_env/bin/python - <<'PY'
for m in ["hmmlearn", "xgboost", "sklearn", "numpy", "pandas"]:
    try:
        mod = __import__(m)
        print(f"  {m}: OK {getattr(mod, '__version__', '')}")
    except Exception as e:
        print(f"  {m}: MISSING ({e})")
PY
echo '=== DONE ==='
