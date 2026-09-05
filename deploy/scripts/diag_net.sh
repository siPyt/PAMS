#!/usr/bin/env bash
echo '=== Can field-nodered resolve "mqtt"? ==='
docker exec field-nodered getent hosts mqtt || echo "  mqtt NOT resolvable"
echo '=== Can field-nodered resolve "field-mqtt"? ==='
docker exec field-nodered getent hosts field-mqtt || echo "  field-mqtt NOT resolvable"
echo '=== Can field-nodered resolve "influxdb"? ==='
docker exec field-nodered getent hosts influxdb || echo "  influxdb NOT resolvable"
echo '=== Can field-nodered resolve "field-influxdb"? ==='
docker exec field-nodered getent hosts field-influxdb || echo "  field-influxdb NOT resolvable"
echo
echo '=== Networks each container is attached to ==='
for c in field-mqtt field-nodered field-influxdb field-grafana; do
    printf '%s: ' "$c"
    docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$c" 2>/dev/null
    echo
done
echo '=== DONE ==='
