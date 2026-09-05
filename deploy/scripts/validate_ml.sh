#!/usr/bin/env bash
cd ~ || exit 1
for f in pams_ml.py pams_ml_service.py ml_selftest.py PAMS_Commands.txt; do
    sed -i 's/\r$//' "$f"
done
sed -i 's/\r$//' ~/field-gateway/node-red-data/flows.json

echo '--- syntax check ---'
~/pams_env/bin/python -m py_compile ~/pams_ml.py ~/pams_ml_service.py && echo 'py OK'

echo '--- flows.json valid JSON? ---'
~/pams_env/bin/python -c 'import json; json.load(open("/home/admin/field-gateway/node-red-data/flows.json")); print("flows.json OK")'

echo '--- ML engine selftest ---'
~/pams_env/bin/python ~/ml_selftest.py
echo '--- DONE ---'
