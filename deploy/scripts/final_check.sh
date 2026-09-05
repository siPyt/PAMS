#!/usr/bin/env bash
echo '=== Node-RED conflict/HttpError lines in last 25s ==='
n=$(docker logs --since 25s field-nodered 2>&1 | grep -icE 'conflict|HttpError')
echo "  count: $n  (0 = clean)"

echo
echo '=== ml_scores writes in last 1m ==='
docker exec field-influxdb influx query \
  'from(bucket:"freezer_data") |> range(start: -1m) |> filter(fn: (r) => r._measurement == "ml_scores" and r._field == "ml_health_score") |> count() |> group() |> sum()' \
  2>&1 | tail -n 6

echo
echo '=== readings writes in last 1m ==='
docker exec field-influxdb influx query \
  'from(bucket:"freezer_data") |> range(start: -1m) |> filter(fn: (r) => r._measurement == "readings" and r._field == "temperature") |> count() |> group() |> sum()' \
  2>&1 | tail -n 6

echo
echo '=== sample enriched ml_scores record (latest per field, FRZ-001) ==='
docker exec field-influxdb influx query \
  'from(bucket:"freezer_data") |> range(start: -1m) |> filter(fn: (r) => r._measurement == "ml_scores" and r.unit_id == "FRZ-001") |> last() |> keep(columns:["_field","_value"])' \
  2>&1 | tail -n 12
echo '=== DONE ==='
