#!/usr/bin/env bash
temp() { vcgencmd measure_temp 2>/dev/null | sed 's/temp=//'; }
fan()  { cat /sys/class/hwmon/hwmon2/fan1_input 2>/dev/null || echo '?'; }
state(){ cat /sys/class/thermal/cooling_device0/cur_state 2>/dev/null || echo '?'; }

echo "baseline:  temp=$(temp)  fan=$(fan) RPM  state=$(state)/4"
CORES=$(nproc)
echo "starting CPU load on ${CORES} cores for 60s..."
for i in $(seq "$CORES"); do timeout 60 sh -c 'while :; do :; done' & done

for s in 10 20 30 40 50 60; do
    sleep 10
    echo "  t+${s}s:  temp=$(temp)  fan=$(fan) RPM  state=$(state)/4"
done
wait 2>/dev/null

echo "load ended; cooling 10s..."
sleep 10
echo "after:     temp=$(temp)  fan=$(fan) RPM  state=$(state)/4"
echo "=== DONE ==="
