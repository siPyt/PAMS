#!/usr/bin/env bash
# Reset the demo to a clean state after the cascade_fail runaway:
# clear ML rolling history + restart the simulator (temps back to ~-20C).
# Run with: sudo bash /home/admin/reset_demo.sh
echo '=== clear ML rolling history (contains cascade spike data) ==='
rm -f /home/admin/pams_ml_data/*.csv 2>/dev/null && echo '  cleared' || echo '  (nothing to clear)'

echo '=== restart simulator (reinitializes temps to ~-20C) ==='
systemctl restart pams-sim && echo '  pams-sim restarted'

echo '=== restart ML (fresh baseline) ==='
systemctl restart pams-ml && echo '  pams-ml restarted'

sleep 10
echo '=== latest ML output (should show ~-20C now) ==='
journalctl -u pams-ml -n 5 --no-pager -o cat
echo '=== DONE ==='
