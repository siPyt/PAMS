#!/usr/bin/env bash
echo '=== flows_cred.json (did the credential persist?) ==='
cat "$HOME/field-gateway/node-red-data/flows_cred.json" 2>/dev/null; echo

echo
echo '=== Node-RED logs (influx-related, last 90s) ==='
docker logs --since 90s field-nodered 2>&1 | grep -iE 'influx|error|econnrefused|unauthor|token|missing' | tail -n 15 || echo '(none)'

echo
echo '=== ml_scores in InfluxDB (last 3m) ==='
docker exec field-influxdb influx query \
  'from(bucket:"freezer_data") |> range(start: -3m) |> filter(fn: (r) => r._measurement == "ml_scores") |> limit(n: 6)' \
  2>&1 | head -n 25

echo
echo '=== raw readings in InfluxDB (last 3m) ==='
docker exec field-influxdb influx query \
  'from(bucket:"freezer_data") |> range(start: -3m) |> filter(fn: (r) => r._measurement == "readings") |> limit(n: 4)' \
  2>&1 | head -n 20
echo '=== DONE ==='
