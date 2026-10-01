#!/usr/bin/env bash
# fix_eth0_flap.sh — stabilize the Pi 5 wired port (eth0 / the 169.254.7.x direct cable)
# that link-flaps (dmesg "Link is Up/Down", renegotiating 10Mb<->1Gb).
#
# What it does (all eth0-only; your WiFi/wlan0 shell is NOT touched):
#   1. installs ethtool if missing
#   2. caps eth0 at a stable 100baseT/Full (negotiated) so a marginal cable stops
#      renegotiating gigabit (gigabit needs all 4 pairs; 100full needs only 2)
#   3. disables EEE / "green ethernet" (the #1 software cause of PHY flapping)
#   4. stops the DHCP churn (ipv4.may-fail yes) so a cable with no DHCP server
#      doesn't hold the link in "connecting (getting IP configuration)"
#   5. persists across reboot + every relink via a NetworkManager dispatcher
#
# RUN WITH SUDO (over your WiFi shell):   sudo bash ~/fix_eth0_flap.sh
set -euo pipefail

IF=eth0
NMCON=netplan-eth0
DISP=/etc/NetworkManager/dispatcher.d/50-eth0-stable
ADV_100FULL=0x008            # ethtool advertise bitmask: 100baseT/Full only

log(){ echo "[fix] $*"; }

if [ "$(id -u)" != 0 ]; then
  echo "Please run with sudo:  sudo bash $0"
  exit 1
fi

# 1) ensure ethtool ---------------------------------------------------------
if ! command -v ethtool >/dev/null 2>&1; then
  log "installing ethtool (needs internet; WiFi is up so this is fine)..."
  apt-get update -qq || true
  apt-get install -y ethtool
fi
ETHTOOL="$(command -v ethtool)"

# 2+3) apply now: negotiate but cap at 100full, and kill EEE ----------------
log "capping $IF at 100baseT/Full (stable on a marginal cable)"
if ! "$ETHTOOL" -s "$IF" autoneg on advertise "$ADV_100FULL"; then
  log "advertise path failed; forcing fixed 100/full"
  "$ETHTOOL" -s "$IF" speed 100 duplex full autoneg off || true
fi
log "disabling EEE / green-ethernet on $IF"
"$ETHTOOL" --set-eee "$IF" eee off 2>/dev/null || true

# 4) stop DHCP churn on the direct cable ------------------------------------
if nmcli -t -f NAME con show 2>/dev/null | grep -qx "$NMCON"; then
  log "NM: $NMCON ipv4/ipv6 may-fail yes (keep link-local, don't hang on DHCP)"
  nmcli con mod "$NMCON" ipv4.may-fail yes ipv6.may-fail yes || true
fi

# 5) persist: reapply on every eth0 'up' ------------------------------------
log "installing persistent dispatcher: $DISP"
cat > "$DISP" <<EOF
#!/bin/sh
# Reapply eth0 link stabilisation on every bring-up (survives reboot & relink).
IFACE="\$1"; ACTION="\$2"
[ "\$IFACE" = "$IF" ] || exit 0
case "\$ACTION" in
  up|hostname|dhcp4-change|connectivity-change)
    $ETHTOOL -s $IF autoneg on advertise $ADV_100FULL 2>/dev/null || \
      $ETHTOOL -s $IF speed 100 duplex full autoneg off 2>/dev/null
    $ETHTOOL --set-eee $IF eee off 2>/dev/null
    ;;
esac
exit 0
EOF
chmod 755 "$DISP"

# bounce eth0 to apply cleanly ----------------------------------------------
log "re-applying $IF connection"
nmcli dev disconnect "$IF" 2>/dev/null || true
nmcli con up "$NMCON" 2>/dev/null || true
sleep 3

echo "---------------------------------------------------------------"
log "DONE. Current eth0 state:"
"$ETHTOOL" "$IF" 2>/dev/null | grep -Ei 'speed|duplex|link detected' | sed 's/^/    /' || true
"$ETHTOOL" --show-eee "$IF" 2>/dev/null | grep -Ei 'eee (status|active)|advertised|supported' | sed 's/^/    /' || true
echo "    carrier_changes=$(cat /sys/class/net/$IF/carrier_changes 2>/dev/null)"
echo "---------------------------------------------------------------"
echo "Verify stability with:  bash ~/net_flap_diag.sh eth0 300"
echo "If it STILL flaps at 100full with EEE off, the cable/connector is"
echo "physically bad -> replace the cable (that is the only remaining fix)."
