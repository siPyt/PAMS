#!/usr/bin/env bash
# One-shot root setup for PAMS: install systemd services, enable Docker at boot,
# start ML + simulator, and apply the FT232R MS/TP timing hardening.
# Run with:  sudo bash /home/admin/setup_services.sh
set -u

echo "=== 1. Install systemd unit files ==="
for u in pams-ml.service pams-sim.service pams-bms.service; do
    install -m 644 "/home/admin/$u" "/etc/systemd/system/$u" && echo "  installed $u"
done
systemctl daemon-reload

echo "=== 2. Ensure Docker starts on boot (stack has restart policies) ==="
systemctl enable docker 2>/dev/null && echo "  docker enabled" || echo "  (docker already enabled)"

echo "=== 3. Enable + start ML and simulator ==="
systemctl enable --now pams-ml.service && echo "  pams-ml up"
systemctl enable --now pams-sim.service && echo "  pams-sim up"

echo "=== 4. Keep BMS node installed but DISABLED (enable when wired) ==="
systemctl disable pams-bms.service 2>/dev/null || true

echo "=== 5. FT232R MS/TP timing hardening ==="
echo 'ACTION=="add", SUBSYSTEM=="usb-serial", DRIVER=="ftdi_sio", ATTR{latency_timer}="1"' \
    > /etc/udev/rules.d/99-ftdi-latency.rules
udevadm control --reload && udevadm trigger && echo "  udev latency rule applied"
if command -v setcap >/dev/null 2>&1; then
    setcap 'cap_sys_nice=eip' \
        /home/admin/bacnet-stack/bin/bacrp \
        /home/admin/bacnet-stack/bin/bacwp \
        /home/admin/bacnet-stack/bin/bacwi \
        /home/admin/bacnet-stack/bin/bacepics 2>/dev/null \
        && echo "  cap_sys_nice granted to MS/TP tools" || echo "  (setcap skipped)"
else
    echo "  setcap not installed - skipping (non-fatal)"
fi

echo
echo "=== STATUS ==="
systemctl --no-pager --property=Id,ActiveState,SubState,UnitFileState show \
    pams-ml.service pams-sim.service pams-bms.service | \
    awk -F= '{printf "%s ", $2} /SubState/{print ""}'
echo
echo "Docker containers:"
docker ps --format '  {{.Names}}: {{.Status}}' 2>/dev/null || echo "  (docker ps needs the admin user)"
echo "=== DONE ==="
