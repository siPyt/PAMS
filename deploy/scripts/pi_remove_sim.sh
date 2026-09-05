#!/usr/bin/env bash
# Remove the freezer simulator and all remnants from the Pi.
# Run with: sudo bash /home/admin/pi_remove_sim.sh
echo '=== stop + disable pams-sim ==='
systemctl disable --now pams-sim.service 2>&1 | tail -n 3 || true

echo '=== remove pams-sim systemd unit ==='
rm -f /etc/systemd/system/pams-sim.service \
      /etc/systemd/system/multi-user.target.wants/pams-sim.service
systemctl daemon-reload

echo '=== install cleaned pams-bms unit (no Conflicts ref) ==='
install -m 644 /home/admin/pams-bms.service /etc/systemd/system/pams-bms.service
systemctl daemon-reload

echo '=== remove simulator program + remnants ==='
rm -f  /home/admin/mock_freezer_data.py
rm -f  /home/admin/pams-sim.service
rm -rf /home/admin/__pycache__
rm -f  /home/admin/pams_ml_data/*.csv 2>/dev/null
rm -f  /home/admin/cascade_fail /home/admin/force_healthy 2>/dev/null

echo '=== restart ML (release any sim file handles, clear in-memory sim history) ==='
systemctl restart pams-ml.service && echo '  pams-ml restarted'

echo
echo '=== verify ==='
[ -e /home/admin/mock_freezer_data.py ] && echo 'mock_freezer_data.py: STILL PRESENT' || echo 'mock_freezer_data.py: gone'
[ -e /etc/systemd/system/pams-sim.service ] && echo 'pams-sim unit: STILL PRESENT' || echo 'pams-sim unit: gone'
echo -n 'pams-sim active state: '; systemctl is-active pams-sim.service 2>/dev/null || true
echo -n 'pams-ml active state:  '; systemctl is-active pams-ml.service
echo '=== DONE ==='
