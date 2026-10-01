#!/usr/bin/env bash
# setup_eth0_freezer_net.sh
# Put the Pi 5 wired port (eth0) on the 192.168.1.x FREEZER / BACnet network as a
# STABLE STATIC address, usable for both freezer polling AND a work PC plugged into
# the same switch (no WiFi at work).
#
# Supersedes fix_eth0_flap.sh for this use-case (it also applies the anti-flap).
#
# Decisions (change CFG below if your site differs):
#   * Pi eth0 = 192.168.1.10/24  (won't collide with WiFi .114 or router .254)
#   * NO gateway on eth0 + never-default + metric 700  -> WiFi keeps the default
#     route at home; at work (WiFi off) eth0 is the only link and works cleanly.
#   * Source policy-routing so replies to peers that arrive on eth0 leave via eth0
#     (fixes dual-homed same-subnet when WiFi is also 192.168.1.x at home).
#   * Link capped at 100baseT/Full + EEE off  -> no 10<->1000 flapping. BACnet is
#     low-bandwidth so 100full is plenty and far more reliable on a marginal cable.
#
# RUN WITH SUDO (over your WiFi shell):   sudo bash ~/setup_eth0_freezer_net.sh
set -euo pipefail

# ----- CFG ------------------------------------------------------------------
IF=eth0
NMCON=netplan-eth0
PI_IP=192.168.1.10          # <-- the Pi's fixed address on the freezer network
PREFIX=24
RT_TABLE=100
METRIC=700                  # higher than wlan0 (600) so WiFi stays primary at home
ADV_100FULL=0x008           # ethtool advertise: 100baseT/Full only (stable)
DISP=/etc/NetworkManager/dispatcher.d/50-eth0-stable
# ----------------------------------------------------------------------------

log(){ echo "[eth0-setup] $*"; }

if [ "$(id -u)" != 0 ]; then
  echo "Please run with sudo:  sudo bash $0"
  exit 1
fi

# 1) ethtool ----------------------------------------------------------------
if ! command -v ethtool >/dev/null 2>&1; then
  log "installing ethtool (WiFi is up, so this works)..."
  apt-get update -qq || true
  apt-get install -y ethtool
fi
ETHTOOL="$(command -v ethtool)"

# 2) anti-flap now: cap 100full + disable EEE -------------------------------
log "capping $IF at 100baseT/Full + disabling EEE/green-ethernet"
"$ETHTOOL" -s "$IF" autoneg on advertise "$ADV_100FULL" \
  || "$ETHTOOL" -s "$IF" speed 100 duplex full autoneg off || true
"$ETHTOOL" --set-eee "$IF" eee off 2>/dev/null || true

# 3) static 192.168.1.x on eth0 ---------------------------------------------
if ! nmcli -t -f NAME con show 2>/dev/null | grep -qx "$NMCON"; then
  log "NM connection '$NMCON' not found; creating it bound to $IF"
  nmcli con add type ethernet ifname "$IF" con-name "$NMCON" || true
fi

log "setting $IF static ${PI_IP}/${PREFIX} (no gateway, never-default, metric $METRIC)"
nmcli con mod "$NMCON" \
  ipv4.method manual \
  ipv4.addresses "${PI_IP}/${PREFIX}" \
  ipv4.gateway "" \
  ipv4.never-default yes \
  ipv4.route-metric "$METRIC" \
  ipv4.may-fail no \
  ipv6.method link-local \
  connection.autoconnect yes \
  connection.autoconnect-priority 10

# 4) source policy routing (dual-homed-safe) -- best effort -----------------
log "adding source policy-routing (from ${PI_IP} -> table ${RT_TABLE})"
nmcli con mod "$NMCON" ipv4.routes "192.168.1.0/${PREFIX} table=${RT_TABLE} src=${PI_IP}" 2>/dev/null \
  || log "  (route attr not applied; core static config still fine)"
nmcli con mod "$NMCON" ipv4.routing-rules "priority ${RT_TABLE} from ${PI_IP}/32 table ${RT_TABLE}" 2>/dev/null \
  || log "  (routing-rule not applied; core static config still fine)"

# 5) persist anti-flap across reboot/relink ---------------------------------
log "installing persistent dispatcher $DISP"
cat > "$DISP" <<EOF
#!/bin/sh
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

# 6) bring it up -------------------------------------------------------------
log "bringing $IF up"
nmcli dev disconnect "$IF" 2>/dev/null || true
nmcli con up "$NMCON" 2>/dev/null || true
sleep 3

echo "---------------------------------------------------------------"
log "RESULT:"
ip -br addr show "$IF" | sed 's/^/    /'
echo "    link : $("$ETHTOOL" "$IF" 2>/dev/null | grep -Ei 'Speed|Duplex|Link detected' | tr '\n' ' ')"
echo "    routes:"; ip route show dev "$IF" | sed 's/^/      /'
echo "    rules :"; ip rule show | grep -E "lookup $RT_TABLE" | sed 's/^/      /' || true
echo "    wlan0 (unchanged):"; ip -br addr show wlan0 | sed 's/^/      /'
echo "---------------------------------------------------------------"
echo "eth0 should now be ${PI_IP}. From a PC on the same switch set e.g."
echo "192.168.1.20/24 and 'ping ${PI_IP}'. Verify link stability with:"
echo "    bash ~/net_flap_diag.sh eth0 300 192.168.1.20"
echo "Freezer BACnet/IP discovery over this port is handled by PAMS (iface=eth0)."
