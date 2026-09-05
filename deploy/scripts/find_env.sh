#!/usr/bin/env bash
# Find the environment variables the bacnet-stack MS/TP tools read.
echo "=== BACNET_* getenv usages ==="
grep -rhoE 'getenv\("BACNET_[A-Z_]+"\)' ~/bacnet-stack 2>/dev/null | sort -u
echo
echo "=== Any getenv with MSTP/IFACE/BAUD/MAC nearby (linux port) ==="
grep -rnE 'getenv|BACNET_IFACE|BACNET_MSTP|BACNET_MAX_MASTER|BACNET_MAX_INFO' \
    ~/bacnet-stack/ports/linux ~/bacnet-stack/apps/lib 2>/dev/null | grep -iE 'getenv' | head -n 40
echo "=== DONE ==="
