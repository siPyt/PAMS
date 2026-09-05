#!/usr/bin/env bash
PY=~/pams_env/bin/python
echo '--- classic bacpypes submodules ---'
$PY -c "import pkgutil, bacpypes; print(sorted(m.name for m in pkgutil.iter_modules(bacpypes.__path__)))"
echo '--- mstp module ---'
$PY -c "import bacpypes.mstp as m; print('mstp OK:', [n for n in dir(m) if not n.startswith('_')])" 2>&1 | head -n 3
echo '--- serial subpackage present? ---'
$PY -c "import pkgutil, bacpypes; sp=[m.name for m in pkgutil.iter_modules(bacpypes.__path__) if m.name=='serial']; print('has serial pkg:', sp)"
echo '--- SerialServer import ---'
$PY -c "from bacpypes.serial.serial_comms import SerialServer; print('SerialServer import OK')" 2>&1 | head -n 3
echo '--- alt SerialServer location ---'
$PY -c "from bacpypes.serial import SerialServer; print('bacpypes.serial.SerialServer OK')" 2>&1 | head -n 3
echo '--- installed bacnet-related packages ---'
~/pams_env/bin/pip list 2>/dev/null | grep -iE "bacnet|bac0|mstp|bacpypes"
echo '--- DONE ---'
