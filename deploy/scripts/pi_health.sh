#!/usr/bin/env bash
echo '=== CPU temperature ==='
vcgencmd measure_temp 2>/dev/null || awk '{printf "temp=%.1fC\n",$1/1000}' /sys/class/thermal/thermal_zone0/temp

echo
echo '=== Fan speed (RPM) ==='
found=0
for f in /sys/class/hwmon/hwmon*/fan1_input /sys/devices/platform/cooling_fan/hwmon/hwmon*/fan1_input; do
    if [ -e "$f" ]; then
        rpm=$(cat "$f"); found=1
        echo "  $f = ${rpm} RPM"
    fi
done
[ "$found" = 0 ] && echo "  (no fan tach found)"

echo '=== Cooling device / fan state (0 = off) ==='
for d in /sys/class/thermal/cooling_device*; do
    [ -e "$d/type" ] && echo "  $(cat $d/type): $(cat $d/cur_state)/$(cat $d/max_state)"
done

echo
echo '=== Throttling / power (0x0 = all good) ==='
vcgencmd get_throttled 2>/dev/null

echo
echo '=== Storage (root filesystem) ==='
df -h / | awk 'NR==1 || /\/$/'

echo
echo '=== Memory ==='
free -h

echo
echo '=== Docker disk usage ==='
docker system df 2>/dev/null

echo
echo '=== Uptime / load ==='
uptime
echo '=== DONE ==='
