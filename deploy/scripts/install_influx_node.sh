#!/usr/bin/env bash
set -u
echo '=== Installing node-red-contrib-influxdb into /data ==='
docker exec -w /data field-nodered npm install node-red-contrib-influxdb@0.7.0 2>&1 | tail -n 10

echo
echo '=== Restart Node-RED ==='
docker restart field-nodered >/dev/null && echo restarted
sleep 18

echo
echo '=== Node-RED status (types registered? flows started?) ==='
docker logs --since 30s field-nodered 2>&1 | grep -iE 'influx|missing|started flows|error' | tail -n 10

echo
echo '=== ml_scores in InfluxDB (last 3m) ==='
docker exec field-influxdb influx query \
  'from(bucket:"freezer_data") |> range(start: -3m) |> filter(fn: (r) => r._measurement == "ml_scores") |> limit(n: 6)' \
  2>&1 | head -n 25
echo '=== DONE ==='
