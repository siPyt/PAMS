#!/usr/bin/env bash
echo '=== field-mqtt state / ports / networks / restarts ==='
docker ps -a --format '{{.Names}}  {{.Status}}  {{.Ports}}' | grep field-mqtt
docker inspect -f 'State={{.State.Status}} RestartCount={{.RestartCount}} Networks={{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}} Ports={{.HostConfig.PortBindings}}' field-mqtt

echo
echo '=== field-mqtt logs (tail 25) ==='
docker logs --tail 25 field-mqtt 2>&1

echo
echo '=== networks that exist ==='
docker network ls --format '{{.Name}}'
echo '=== DONE ==='
