#!/usr/bin/env bash
# Reinstall the updated PAMS service units (adds Conflicts=) and reload systemd.
# Run with: sudo bash /home/admin/reinstall_units.sh
install -m 644 /home/admin/pams-sim.service /etc/systemd/system/pams-sim.service && echo '  updated pams-sim.service'
install -m 644 /home/admin/pams-bms.service /etc/systemd/system/pams-bms.service && echo '  updated pams-bms.service'
systemctl daemon-reload && echo '  daemon-reloaded'
echo '--- verify mutual exclusion ---'
systemctl show pams-sim.service -p Conflicts
systemctl show pams-bms.service -p Conflicts
echo DONE
