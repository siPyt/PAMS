#!/usr/bin/env bash
# Apply cap_sys_nice to the REAL bacnet-stack binaries (bin/ entries are symlinks).
# Run with: sudo bash /home/admin/setcap_fix2.sh
BASE=/home/admin/bacnet-stack/bin
for b in bacrp bacwp bacwi bacepics; do
    f=$(readlink -f "$BASE/$b")
    if setcap 'cap_sys_nice=eip' "$f"; then
        echo "set: $f"
    else
        echo "FAILED: $f"
    fi
done
echo '--- verify ---'
for b in bacrp bacwp bacwi bacepics; do
    f=$(readlink -f "$BASE/$b")
    getcap "$f"
done
echo DONE
