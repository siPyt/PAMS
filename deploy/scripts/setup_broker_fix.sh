#!/usr/bin/env bash
# Unify MQTT on the containerized broker: stop the host mosquitto (frees 1883),
# start field-mqtt on the compose network, and reconnect the PAMS services + Node-RED.
# Run with: sudo bash /home/admin/setup_broker_fix.sh
set -u

echo '=== 1. Stop + disable host mosquitto (frees port 1883) ==='
systemctl disable --now mosquitto 2>&1 && echo '  host mosquitto stopped + disabled'
sleep 2

echo
echo '=== 2. Start containerized broker via compose ==='
cd /home/admin/field-gateway || exit 1
docker compose up -d 2>&1 | tail -n 10
sleep 3

echo
echo '=== 3. Broker container state ==='
docker ps --format '{{.Names}}  {{.Status}}  {{.Ports}}' | grep field-mqtt || echo '  field-mqtt not running!'

echo
echo '=== 4. Reconnect PAMS services + Node-RED ==='
systemctl restart pams-ml pams-sim && echo '  pams-ml + pams-sim restarted'
docker restart field-nodered >/dev/null && echo '  field-nodered restarted'
sleep 16

echo
echo '=== 5. Can Node-RED resolve + reach the broker? ==='
docker exec field-nodered getent hosts mqtt && echo '  mqtt resolvable' || echo '  mqtt NOT resolvable'
docker logs --since 15s field-nodered 2>&1 | grep -iE 'connect|mqtt' | tail -n 5

echo
echo '=== 6. Data landing? (ml_scores last 2m) ==='
docker exec field-influxdb influx query \
  'from(bucket:"freezer_data") |> range(start: -2m) |> filter(fn: (r) => r._measurement == "ml_scores") |> limit(n: 5)' \
  2>&1 | head -n 20
echo '=== DONE ==='
