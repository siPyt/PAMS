#!/usr/bin/env bash
BIN=~/bacnet-stack/bin
echo "=== bacrp (read property) usage ==="
"$BIN/bacrp" --help 2>&1 | head -n 45
echo
echo "=== bacwp (write property) usage ==="
"$BIN/bacwp" --help 2>&1 | head -n 60
echo
echo "=== FT232R latency_timer (ms) ==="
cat /sys/bus/usb-serial/devices/ttyUSB0/latency_timer 2>/dev/null || echo "no latency_timer sysfs node"
echo
echo "=== passwordless sudo? ==="
sudo -n true 2>/dev/null && echo "sudo NOPASSWD: yes" || echo "sudo needs a password"
echo "=== DONE ==="
