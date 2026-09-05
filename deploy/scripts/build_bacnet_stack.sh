#!/usr/bin/env bash
# Build Steve Karg's bacnet-stack demo apps with the MS/TP datalink,
# so we can read/write BACnet over the Pi's RS-485 (/dev/ttyUSB0).
set -u
cd ~ || exit 1

if [ ! -d bacnet-stack ]; then
    echo "Cloning bacnet-stack..."
    git clone --depth 1 https://github.com/bacnet-stack/bacnet-stack.git || exit 1
fi

cd bacnet-stack || exit 1
echo "=== Building MS/TP apps (BACDL=mstp) ==="
make BACDL=mstp 2>&1 | tail -n 15

echo
echo "=== Built MS/TP client binaries ==="
find . -maxdepth 3 -type f -perm -111 \
    \( -name bacrp -o -name bacwp -o -name bacwi -o -name bacwh -o -name bacepics \) \
    -printf '%p\n' 2>/dev/null || echo "(none found - build may have failed)"

echo
echo "=== bacwi (who-is) usage smoke test ==="
BWI=$(find . -maxdepth 3 -type f -perm -111 -name bacwi | head -n1)
if [ -n "${BWI:-}" ]; then
    "$BWI" --help 2>&1 | head -n 12 || true
else
    echo "bacwi not built"
fi
echo "=== DONE ==="
