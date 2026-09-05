#!/usr/bin/env bash
# Configure the InfluxDB token for Node-RED headlessly and verify data flow.
# Usage: bash setup_influx_nodered.sh "<INFLUXDB_TOKEN>"
# The token is passed as an argument so it is never stored in the repo.
set -u

TOKEN="${1:-}"
ORG="PAMS_demo"
BUCKET="freezer_data"
NRD="$HOME/field-gateway/node-red-data"

if [ -z "$TOKEN" ]; then
    echo "ERROR: pass the InfluxDB token as the first argument."
    exit 1
fi

echo '=== 1. Verify token via influx CLI ==='
docker exec field-influxdb influx config create --config-name pams \
    --host-url http://localhost:8086 --org "$ORG" --token "$TOKEN" --active >/dev/null 2>&1 \
  || docker exec field-influxdb influx config update --config-name pams \
    --token "$TOKEN" --active >/dev/null 2>&1
docker exec field-influxdb influx bucket list --org "$ORG" 2>&1 | head -n 5

echo
echo '=== 2. Write Node-RED credential (plaintext store) ==='
printf '{"influx-config":{"token":"%s"}}' "$TOKEN" > "$NRD/flows_cred.json"
echo "wrote $NRD/flows_cred.json"

echo
echo '=== 3. Disable credential encryption in settings.js ==='
if grep -q "credentialSecret: false" "$NRD/settings.js"; then
    echo "already set"
else
    sed -i '0,/module.exports = {/s//module.exports = {\n    credentialSecret: false,/' "$NRD/settings.js"
    echo "set credentialSecret: false"
fi

echo
echo '=== 4. Restart Node-RED ==='
docker restart field-nodered >/dev/null && echo "restarted; waiting for startup..."
sleep 14

echo
echo '=== 5. Verify ml_scores landing (last 3m) ==='
docker exec field-influxdb influx query \
  "from(bucket:\"$BUCKET\") |> range(start: -3m) |> filter(fn: (r) => r._measurement == \"ml_scores\") |> limit(n: 5)" \
  2>&1 | head -n 25
echo '=== DONE ==='
