#!/usr/bin/env python3
"""
PAMS BACnet/IP helper (bacpypes) - runs under the venv python, called by the
gateway for BACnet/IP discovery since the bacnet-stack CLI tools are MS/TP-only.

Commands (each prints ONE JSON line):
  whois [target]          -> {"devices":[{"instance":N,"address":"ip:port"}], ...}
  scan  <device> [target] -> {"objects":[{type,short,instance,object,name,value,units,suggest}], ...}

`target` (optional) = a specific device IP[:port] for a directed request across
subnets; otherwise a local broadcast Who-Is is used. Interface bind auto-detects
the primary IPv4 (override with PAMS_BIP_BIND, e.g. 192.168.1.112/24).
"""
import sys
import os
import re
import json
import socket
import threading
import time

from bacpypes.core import run, stop
from bacpypes.pdu import Address
from bacpypes.app import BIPSimpleApplication
from bacpypes.local.device import LocalDeviceObject
from bacpypes.apdu import WhoIsRequest, ReadPropertyRequest
from bacpypes.iocb import IOCB
from bacpypes.primitivedata import Unsigned, CharacterString, Real, Enumerated, Integer
from bacpypes.constructeddata import ArrayOf
from bacpypes.object import get_datatype

OBJ_SHORT = {
    "analogInput": "AI", "analogOutput": "AO", "analogValue": "AV",
    "binaryInput": "BI", "binaryOutput": "BO", "binaryValue": "BV",
    "multiStateInput": "MSI", "multiStateOutput": "MSO", "multiStateValue": "MSV",
    "characterstringValue": "CSV",
}
DASHED = {
    "analogInput": "analog-input", "analogOutput": "analog-output", "analogValue": "analog-value",
    "binaryInput": "binary-input", "binaryOutput": "binary-output", "binaryValue": "binary-value",
    "multiStateInput": "multi-state-input", "multiStateOutput": "multi-state-output",
    "multiStateValue": "multi-state-value",
    "characterstringValue": "characterstring-value",
}


def primary_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.1", 1))
        return s.getsockname()[0]
    except Exception:
        return "0.0.0.0"
    finally:
        s.close()


def suggest_channel(name):
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name or "")
    s = re.sub(r"[^0-9A-Za-z]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_").lower() or "point"


def make_app():
    bind = os.environ.get("PAMS_BIP_BIND") or (primary_ip() + "/24")
    dev = LocalDeviceObject(objectName="PAMSGateway", objectIdentifier=("device", 599000),
                            maxApduLengthAccepted=1024, segmentationSupported="noSegmentation",
                            vendorIdentifier=15)
    found = {}

    class App(BIPSimpleApplication):
        def do_IAmRequest(self, apdu):
            inst = apdu.iAmDeviceIdentifier[1]
            found[inst] = apdu.pduSource

    app = App(dev, Address(bind))
    threading.Thread(target=run, daemon=True).start()
    return app, found


def do_whois(target=None, wait=5.0):
    app, found = make_app()
    req = WhoIsRequest()
    req.pduDestination = Address(target) if target else Address(primary_ip().rsplit(".", 1)[0] + ".255")
    app.request(req)
    time.sleep(wait)
    stop()
    devices = [{"instance": i, "address": str(a)} for i, a in found.items()]
    return {"devices": devices, "datalink": "bip", "target": target,
            "note": "" if devices else "no BACnet/IP devices answered", "ts": time.time()}


def _read(app, addr, obj_type, obj_inst, prop, timeout=5):
    req = ReadPropertyRequest(objectIdentifier=(obj_type, obj_inst), propertyIdentifier=prop)
    req.pduDestination = addr
    iocb = IOCB(req)
    app.request_io(iocb)
    iocb.wait(timeout)
    if iocb.ioError or not iocb.ioResponse:
        return None
    apdu = iocb.ioResponse
    datatype = get_datatype(apdu.objectIdentifier[0], apdu.propertyIdentifier)
    if datatype is None:
        return None
    try:
        return apdu.propertyValue.cast_out(datatype)
    except Exception:
        return None


def do_scan(device, target=None, limit=250):
    app, found = make_app()
    # Discover the device address (directed or broadcast Who-Is), match instance.
    req = WhoIsRequest(deviceInstanceRangeLowLimit=int(device), deviceInstanceRangeHighLimit=int(device))
    req.pduDestination = Address(target) if target else Address(primary_ip().rsplit(".", 1)[0] + ".255")
    app.request(req)
    deadline = time.time() + 6
    while time.time() < deadline and int(device) not in found:
        time.sleep(0.2)
    addr = found.get(int(device)) or (Address(target) if target else None)
    if addr is None:
        stop()
        return {"objects": [], "device": device, "note": "device did not respond to Who-Is", "ts": time.time()}

    objlist = _read(app, addr, "device", int(device), "objectList")
    objects = []
    if objlist:
        for oid in objlist[:limit]:
            otype, oinst = oid[0], oid[1]
            if otype not in OBJ_SHORT:
                continue
            name = _read(app, addr, otype, oinst, "objectName")
            pv = _read(app, addr, otype, oinst, "presentValue")
            units = _read(app, addr, otype, oinst, "units") if otype.startswith("analog") else None
            nm = str(name) if name is not None else f"{OBJ_SHORT[otype]}{oinst}"
            objects.append({
                "type": DASHED[otype], "short": OBJ_SHORT[otype], "instance": oinst,
                "object": f"{DASHED[otype]}:{oinst}", "name": nm,
                "value": None if pv is None else str(pv),
                "units": None if units is None else str(units),
                "suggest": suggest_channel(nm),
            })
    stop()
    note = "" if objects else "no readable objects (object-list empty or unreadable)"
    return {"objects": objects, "device": device, "count": len(objects), "note": note, "ts": time.time()}


def main():
    args = sys.argv[1:]
    try:
        if args and args[0] == "whois":
            out = do_whois(args[1] if len(args) > 1 else None)
        elif args and args[0] == "scan" and len(args) >= 2:
            out = do_scan(args[1], args[2] if len(args) > 2 else None)
        else:
            out = {"error": "usage: whois [target] | scan <device> [target]"}
    except Exception as e:  # noqa: BLE001
        out = {"error": str(e), "devices": [], "objects": []}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()   # flush BEFORE the hard exit or the output is lost
    os._exit(0)          # bacpypes core thread doesn't stop cleanly; force exit


if __name__ == "__main__":
    main()
