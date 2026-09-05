#!/usr/bin/env bash
# PAMS Pi discovery: serial/RS-485 adapter, user perms, BACnet libs.
echo "=== WHOAMI / GROUPS ==="
id
echo
echo "=== SERIAL DEVICES ==="
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null || echo "no ttyUSB/ttyACM found"
echo
echo "=== USB DEVICES (lsusb) ==="
lsusb
echo
echo "=== USB-SERIAL KERNEL MESSAGES ==="
dmesg 2>/dev/null | grep -iE "ch341|ft232|ftdi|cp210|pl2303|ttyUSB|usbserial" | tail -n 20 || echo "(dmesg needs root, or nothing matched)"
echo
echo "=== udev serial-by-id ==="
ls -l /dev/serial/by-id/ 2>/dev/null || echo "(no /dev/serial/by-id)"
echo
echo "=== BACPYPES (classic) ==="
~/pams_env/bin/pip show bacpypes 2>/dev/null | grep -E "^(Name|Version)" || echo "classic bacpypes not found"
echo
echo "=== BACPYPES3 ==="
~/pams_env/bin/python -c "import bacpypes3; print('bacpypes3', bacpypes3.__version__)" 2>/dev/null || echo "bacpypes3 NOT installed"
echo
echo "=== pyserial ==="
~/pams_env/bin/python -c "import serial; print('pyserial', serial.__version__)" 2>/dev/null || echo "pyserial NOT installed"
echo
echo "=== DONE ==="
