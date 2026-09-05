#!/usr/bin/env bash
echo '=== Node-RED log (tail 50) ==='
docker logs --tail 50 field-nodered 2>&1

echo
echo '=== mqtt-broker + mqtt-in nodes in flows.json ==='
python3 - <<'PY'
import json
d = json.load(open("/home/admin/field-gateway/node-red-data/flows.json"))
for n in d:
    if n.get("type") in ("mqtt-broker", "mqtt in"):
        print(n.get("type"), "id=", n.get("id"), "broker=", n.get("broker"),
              "credentials=", "credentials" in n)
PY
echo '=== DONE ==='
