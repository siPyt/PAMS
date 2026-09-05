#!/usr/bin/env bash
# Fix the InfluxDB door_status type conflict by clearing stale 'readings' data,
# reconfigure the influx CLI, and verify both measurements now write.
# Usage: bash fix_influx_schema.sh "<INFLUXDB_TOKEN>"
set -u
TOKEN="${1:-}"
ORG="PAMS_demo"
BUCKET="freezer_data"
[ -z "$TOKEN" ] && { echo "ERROR: pass token as arg 1"; exit 1; }

echo '=== configure influx CLI ==='
docker exec field-influxdb influx config create --config-name pams \
    --host-url http://localhost:8086 --org "$ORG" --token "$TOKEN" --active >/dev/null 2>&1 \
  || docker exec field-influxdb influx config update --config-name pams \
    --token "$TOKEN" --active >/dev/null 2>&1
docker exec field-influxdb influx bucket list --org "$ORG" 2>&1 | head -n 3

echo
echo '=== delete stale readings data (clears door_status int/float conflict) ==='
docker exec field-influxdb influx delete --bucket "$BUCKET" --org "$ORG" \
    --start 1970-01-01T00:00:00Z --stop 2035-01-01T00:00:00Z \
    --predicate '_measurement="readings"' 2>&1 && echo '  cleared old readings'

echo
echo '=== wait for fresh writes ==='
sleep 10

echo '=== ml_scores (last 2m) ==='
docker exec field-influxdb influx query \
  "from(bucket:\"$BUCKET\") |> range(start: -2m) |> filter(fn: (r) => r._measurement == \"ml_scores\") |> keep(columns:[\"_field\",\"_value\",\"unit_id\"]) |> limit(n: 6)" \
  2>&1 | head -n 20

echo
echo '=== readings (last 2m) ==='
docker exec field-influxdb influx query \
  "from(bucket:\"$BUCKET\") |> range(start: -2m) |> filter(fn: (r) => r._measurement == \"readings\") |> keep(columns:[\"_field\",\"_value\",\"unit_id\"]) |> limit(n: 6)" \
  2>&1 | head -n 20

echo
echo '=== recent Node-RED influx errors? ==='
docker logs --since 25s field-nodered 2>&1 | grep -iE 'conflict|influx.*error|HttpError' | tail -n 4 || echo '  none'
echo '=== DONE ==='
