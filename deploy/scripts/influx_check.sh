#!/usr/bin/env bash
# Inspect InfluxDB: CLI config, tokens, buckets, and whether data is landing.
echo '=== influx CLI config (is the CLI authenticated?) ==='
docker exec field-influxdb influx config ls 2>&1 | head

echo
echo '=== auth tokens ==='
docker exec field-influxdb influx auth list 2>&1 | head

echo
echo '=== buckets ==='
docker exec field-influxdb influx bucket list 2>&1 | head

echo
echo '=== recent data in freezer_data (last 5m) ==='
docker exec field-influxdb influx query 'from(bucket:"freezer_data") |> range(start: -5m) |> limit(n: 3)' 2>&1 | head -n 20

echo
echo '=== recent ml_scores measurement (last 5m) ==='
docker exec field-influxdb influx query 'from(bucket:"freezer_data") |> range(start: -5m) |> filter(fn: (r) => r._measurement == "ml_scores") |> limit(n: 3)' 2>&1 | head -n 20
echo '=== DONE ==='
