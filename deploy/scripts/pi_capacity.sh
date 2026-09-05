#!/usr/bin/env bash
echo '=== MEMORY ==='
free -h | awk 'NR==1 || /Mem:/ || /Swap:/'
echo
echo '=== DISK (root filesystem) ==='
df -h / | awk 'NR==1 || /\/$/'
echo
echo '=== DOCKER TOTAL ==='
docker system df
echo
echo '=== DOCKER VOLUMES (per-volume size: DB, Grafana, etc.) ==='
docker system df -v 2>/dev/null | grep -A 12 'VOLUME NAME'
echo '=== DONE ==='
