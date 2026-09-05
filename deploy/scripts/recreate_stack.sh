#!/usr/bin/env bash
set -u
cd /home/admin/field-gateway || exit 1

echo '=== compose down (containers only; named volumes + bind mounts persist) ==='
docker compose down 2>&1 | tail -n 8

echo
echo '=== compose up -d (recreate cleanly on field-gateway_default) ==='
docker compose up -d 2>&1 | tail -n 10
sleep 10

echo
echo '=== field-mqtt state / network / ports ==='
docker inspect -f 'State={{.State.Status}} Networks={{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' field-mqtt
docker ps --format '{{.Names}}  {{.Ports}}' | grep field-mqtt

echo
echo '=== Node-RED resolve + reach broker ==='
docker exec field-nodered getent hosts mqtt && echo '  mqtt resolvable' || echo '  mqtt NOT resolvable'
echo '=== DONE ==='
