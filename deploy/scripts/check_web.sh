#!/usr/bin/env bash
for p in 1880 3000 8086; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 "http://localhost:$p/" 2>/dev/null)
    echo "port $p -> HTTP $code"
done
echo '--- containers ---'
docker ps --format '{{.Names}}: {{.Status}}'
