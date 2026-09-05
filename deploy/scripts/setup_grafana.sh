#!/usr/bin/env bash
# Provision Grafana: InfluxDB datasource (Flux) + PAMS dashboard, via the API.
# Usage: bash setup_grafana.sh "<INFLUXDB_TOKEN>"   (token passed at runtime, not stored)
set -u
TOKEN="${1:-}"
G="http://localhost:3000"
AUTH="admin:admin"
[ -z "$TOKEN" ] && { echo "ERROR: pass the InfluxDB token as arg 1"; exit 1; }

read -r -d '' DS <<JSON
{
  "uid": "influxdb_pams",
  "name": "InfluxDB-PAMS",
  "type": "influxdb",
  "access": "proxy",
  "url": "http://influxdb:8086",
  "isDefault": true,
  "jsonData": {"version":"Flux","organization":"PAMS_demo","defaultBucket":"freezer_data","httpMode":"POST"},
  "secureJsonData": {"token":"$TOKEN"}
}
JSON

echo '=== 1. Create InfluxDB datasource ==='
resp=$(curl -s -u "$AUTH" -X POST "$G/api/datasources" -H 'Content-Type: application/json' -d "$DS")
echo "$resp"
if echo "$resp" | grep -qi "already exists"; then
    echo '--- datasource exists; updating ---'
    curl -s -u "$AUTH" -X PUT "$G/api/datasources/uid/influxdb_pams" -H 'Content-Type: application/json' -d "$DS"; echo
fi

echo
echo '=== 2. Datasource health check ==='
curl -s -u "$AUTH" "$G/api/datasources/uid/influxdb_pams/health"; echo

echo
echo '=== 3. Import PAMS dashboard ==='
curl -s -u "$AUTH" -X POST "$G/api/dashboards/db" -H 'Content-Type: application/json' \
    -d @/home/admin/pams_dashboard.json; echo
echo '=== DONE (open http://alpha-p:3000 -> Dashboards -> PAMS Freezer Health) ==='
