#!/usr/bin/env bash
# Re-provision the PAMS Grafana dashboard from ~/pams_dashboard.json
set -e
python3 -c 'import json;json.load(open("/home/admin/pams_dashboard.json"));print("dashboard JSON OK")'
echo "--- pushing to Grafana ---"
curl -s -X POST http://localhost:3000/api/dashboards/db \
  -H "Content-Type: application/json" \
  -u admin:admin \
  --data-binary @/home/admin/pams_dashboard.json
echo
