#!/usr/bin/env bash
# net_flap_diag.sh — diagnose Ethernet "port going up and down" (link flapping) on alpha-p.
#
# Captures a baseline, then watches each wired interface for carrier (link) changes,
# speed/duplex renegotiation, and NIC error counters over a window, and prints a verdict.
#
# Usage:
#   ./net_flap_diag.sh                      # watch eth0 for 120s
#   ./net_flap_diag.sh eth0 300             # watch eth0 for 300s
#   ./net_flap_diag.sh eth0 300 169.254.7.2 # also ping-test the PC on the cable
#   ./net_flap_diag.sh "eth0 wlan0" 180
#
# Safe/read-only. No sudo required (uses sudo -n opportunistically for ethtool stats).
set -u

IFACES="${1:-eth0}"
DURATION="${2:-120}"
PEER="${3:-}"               # optional: PC IP on the 169.254.7.x cable to ping-test
SAMPLE=2                     # seconds between samples
LOG="${HOME}/net_flap_$(date +%Y%m%d_%H%M%S).log"

ts()   { date '+%Y-%m-%d %H:%M:%S'; }
line() { printf '%s\n' "----------------------------------------------------------------"; }
say()  { echo "$@" | tee -a "$LOG"; }

have() { command -v "$1" >/dev/null 2>&1; }

read_int() { # read_int <path> -> prints integer or 0
  local v; v="$(cat "$1" 2>/dev/null)"; [[ "$v" =~ ^-?[0-9]+$ ]] && echo "$v" || echo 0
}

iface_speed() { # best-effort current speed/duplex without failing
  local i="$1" s d
  s="$(cat "/sys/class/net/$i/speed" 2>/dev/null)"
  d="$(cat "/sys/class/net/$i/duplex" 2>/dev/null)"
  [[ -z "$s" || "$s" == "-1" ]] && s="?"
  [[ -z "$d" ]] && d="?"
  echo "${s}Mb/${d}"
}

ethtool_stats() { # dump error-ish counters if ethtool + perms allow
  local i="$1"
  if have ethtool; then
    sudo -n ethtool -S "$i" 2>/dev/null || ethtool -S "$i" 2>/dev/null
  fi
}

say "PAMS network link-flap diagnostic"
say "started : $(ts)"
say "host    : $(hostname)"
say "ifaces  : $IFACES"
say "duration: ${DURATION}s  sample: ${SAMPLE}s"
say "log     : $LOG"
line | tee -a "$LOG"

# ---------- Baseline ----------
say "== BASELINE =="
for i in $IFACES; do
  if [[ ! -e "/sys/class/net/$i" ]]; then
    say "  $i : NOT PRESENT"
    continue
  fi
  oper="$(cat /sys/class/net/$i/operstate 2>/dev/null)"
  carr="$(cat /sys/class/net/$i/carrier 2>/dev/null)"
  cc="$(read_int /sys/class/net/$i/carrier_changes)"
  cu="$(read_int /sys/class/net/$i/carrier_up_count)"
  cd="$(read_int /sys/class/net/$i/carrier_down_count)"
  ipaddr="$(ip -br -4 addr show "$i" 2>/dev/null | awk '{$1=$1;print}')"
  say "  $i : oper=$oper carrier=$carr speed=$(iface_speed "$i") carrier_changes=$cc (up=$cu down=$cd)"
  say "       addr: ${ipaddr:-<none>}"
done
line | tee -a "$LOG"

# snapshot starting counters
declare -A START_CC START_RXERR START_TXERR
for i in $IFACES; do
  [[ -e "/sys/class/net/$i" ]] || continue
  START_CC[$i]="$(read_int /sys/class/net/$i/carrier_changes)"
  START_RXERR[$i]="$(read_int /sys/class/net/$i/statistics/rx_errors)"
  START_TXERR[$i]="$(read_int /sys/class/net/$i/statistics/tx_errors)"
done

say "== WATCHING (press Ctrl-C to stop early) =="
say "   time     iface  carrier speed       cc   note"

end=$(( $(date +%s) + DURATION ))
declare -A LAST_CARR LAST_SPEED
flaps=0
while [[ $(date +%s) -lt $end ]]; do
  for i in $IFACES; do
    [[ -e "/sys/class/net/$i" ]] || continue
    carr="$(cat /sys/class/net/$i/carrier 2>/dev/null)"
    spd="$(iface_speed "$i")"
    cc="$(read_int /sys/class/net/$i/carrier_changes)"
    note=""
    prevc="${LAST_CARR[$i]:-}"
    prevs="${LAST_SPEED[$i]:-}"
    if [[ -n "$prevc" && "$prevc" != "$carr" ]]; then
      note="LINK $([[ "$carr" == "1" ]] && echo UP || echo DOWN)  <<< FLAP"
      flaps=$((flaps+1))
    elif [[ -n "$prevs" && "$prevs" != "$spd" && "$carr" == "1" ]]; then
      note="renegotiated $prevs -> $spd"
    fi
    if [[ -n "$note" ]]; then
      say "$(date '+%H:%M:%S')  $i  carrier=$carr  $spd  cc=$cc  $note"
    fi
    LAST_CARR[$i]="$carr"
    LAST_SPEED[$i]="$spd"
  done
  sleep "$SAMPLE"
done

line | tee -a "$LOG"
say "== SUMMARY =="
for i in $IFACES; do
  [[ -e "/sys/class/net/$i" ]] || continue
  now_cc="$(read_int /sys/class/net/$i/carrier_changes)"
  d_cc=$(( now_cc - ${START_CC[$i]:-0} ))
  now_rx="$(read_int /sys/class/net/$i/statistics/rx_errors)"
  now_tx="$(read_int /sys/class/net/$i/statistics/tx_errors)"
  d_rx=$(( now_rx - ${START_RXERR[$i]:-0} ))
  d_tx=$(( now_tx - ${START_TXERR[$i]:-0} ))
  say "  $i : carrier_changes +$d_cc during window | rx_err +$d_rx | tx_err +$d_tx | now $(iface_speed "$i")"
done
say "  observed transitions this run: $flaps"
line | tee -a "$LOG"

say "== KERNEL LINK EVENTS (dmesg, last 40) =="
if have dmesg; then
  (dmesg -T 2>/dev/null || dmesg 2>/dev/null) | grep -iE 'Link is (Up|Down)|carrier|renamed|under-voltage|phy|macb' | tail -40 | tee -a "$LOG"
fi
line | tee -a "$LOG"

say "== ETHTOOL ERROR COUNTERS =="
for i in $IFACES; do
  [[ -e "/sys/class/net/$i" ]] || continue
  say "  --- $i ---"
  ethtool_stats "$i" | grep -iE 'err|drop|crc|fcs|fifo|carrier|collision|flush|reset' | sed 's/^/    /' | tee -a "$LOG"
done
line | tee -a "$LOG"

# ---------- Verdict ----------
say "== VERDICT =="
total_d=0
for i in $IFACES; do
  [[ -e "/sys/class/net/$i" ]] || continue
  now_cc="$(read_int /sys/class/net/$i/carrier_changes)"
  d_cc=$(( now_cc - ${START_CC[$i]:-0} ))
  total_d=$(( total_d + d_cc ))
  spd="$(cat /sys/class/net/$i/speed 2>/dev/null)"
  carr="$(cat /sys/class/net/$i/carrier 2>/dev/null)"
  if [[ "$d_cc" -ge 2 ]]; then
    say "  [$i] FLAPPING: link changed $d_cc times in ${DURATION}s."
    if [[ "$spd" == "10" || "$spd" == "100" ]]; then
      say "        Negotiated only ${spd}Mb (not 1Gb) => strong sign of a DAMAGED CABLE"
      say "        (a broken twisted pair forces 10/100 fallback). Swap the cable first."
    else
      say "        Physical-layer instability: suspect cable, RJ45 seating, or switch port."
    fi
  elif [[ "$carr" != "1" ]]; then
    say "  [$i] DOWN: no carrier (nothing linked / dead port / unplugged)."
  else
    say "  [$i] STABLE during this window (speed $(iface_speed "$i")). If users still see"
    say "        flaps, run longer (e.g. 600s) to catch intermittent drops."
  fi
  # eth0 here is the direct-cable 169.254.7.x link PCs connect to.
  if [[ "$i" == "eth0" ]]; then
    ll="$(ip -br -4 addr show eth0 2>/dev/null | grep -o '169\.254\.[0-9.]*')"
    [[ -n "$ll" ]] && say "        note: eth0 carries link-local $ll (your 169.254.7.x direct cable)."
  fi
done

if [[ -n "$PEER" ]]; then
  say "== PEER PING TEST ($PEER) =="
  pstat="$(ping -c 20 -i 0.3 -W 1 "$PEER" 2>/dev/null | tail -3)"
  if [[ -n "$pstat" ]]; then
    say "$pstat"
    say "  (any packet loss / spikes here = the flapping cable dropping your PC link)"
  else
    say "  no reply from $PEER — PC unreachable over the cable right now."
  fi
  line | tee -a "$LOG"
fi
say "  Full log saved: $LOG"
say "finished: $(ts)"
