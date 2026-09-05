#!/usr/bin/env bash
# Prove the MS/TP stack can open the FTDI port and run the token FSM.
# No wires landed yet, so expect: port opens, sole-master, no devices found.
export BACNET_DATALINK=mstp
export BACNET_IFACE=/dev/ttyUSB0
export BACNET_MSTP_IFACE=/dev/ttyUSB0
export BACNET_MSTP_BAUD=38400
export BACNET_MSTP_MAC=45
export BACNET_MAX_MASTER=127
export BACNET_MAX_INFO_FRAMES=1
export BACNET_MSTP_DEBUG=1
export BACNET_DATALINK_DEBUG=1

echo "=== fuser check: is anything else holding the port? ==="
fuser /dev/ttyUSB0 2>/dev/null && echo "(port in use!)" || echo "port free"
echo
echo "=== Who-Is over MS/TP (8s, expect no devices, port should open) ==="
timeout 8 ~/bacnet-stack/bin/bacwi 2>&1 | head -n 40
echo "--- (timeout/exit is expected) ---"
echo "=== DONE ==="
