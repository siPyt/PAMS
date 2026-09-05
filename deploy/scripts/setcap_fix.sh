#!/usr/bin/env bash
# Install libcap2-bin and grant cap_sys_nice to the bacnet-stack MS/TP tools
# so they can use real-time thread priority for reliable MS/TP token timing.
# Run with: sudo bash /home/admin/setcap_fix.sh
set -u
echo '=== install libcap2-bin ==='
apt-get update -qq >/dev/null 2>&1
apt-get install -y libcap2-bin 2>&1 | tail -n 3

echo
echo '=== apply cap_sys_nice to MS/TP tools ==='
setcap 'cap_sys_nice=eip' \
    /home/admin/bacnet-stack/bin/bacrp \
    /home/admin/bacnet-stack/bin/bacwp \
    /home/admin/bacnet-stack/bin/bacwi \
    /home/admin/bacnet-stack/bin/bacepics && echo '  applied'

echo
echo '=== verify ==='
getcap /home/admin/bacnet-stack/bin/bacwi /home/admin/bacnet-stack/bin/bacrp
echo '=== DONE ==='
