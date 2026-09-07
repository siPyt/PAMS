#!/usr/bin/env python3
"""
PAMS BACnet/IP poller (bacpypes) - polls a BACnet/IP device's mapped points and
publishes them to MQTT (pams/freezers/<unit>) so the ML + dashboards pick them up.
Resolves the device address via Who-Is (robust to ephemeral ports). Runs under the
venv python (has bacpypes + paho).

Env:
  UNIT_ID           unit name (default SIM-ROOM)
  DEVICE_INSTANCE   BACnet device instance to poll (required)
  PAMS_POINTS_FILE  channel->object map (default ~/pams_points.json)
  TEMP_CHANNEL      which mapped channel is the primary 'temperature' (optional;
                    else first channel starting with 'temperature')
  MQTT_HOST/PORT    broker (default localhost:1883)
  POLL_SECONDS      poll interval (default 5)
  PAMS_BIP_BIND     bind CIDR (default auto primary IPv4 /24)
"""
import os
import json
import time
import socket
import threading

import paho.mqtt.client as mqtt
from bacpypes.core import run
from bacpypes.pdu import Address
from bacpypes.app import BIPSimpleApplication
from bacpypes.local.device import LocalDeviceObject
from bacpypes.apdu import WhoIsRequest, ReadPropertyRequest
from bacpypes.iocb import IOCB
from bacpypes.object import get_datatype

CAMEL = {
    "analog-input": "analogInput", "analog-output": "analogOutput", "analog-value": "analogValue",
    "binary-input": "binaryInput", "binary-output": "binaryOutput", "binary-value": "binaryValue",
    "multi-state-input": "multiStateInput", "multi-state-output": "multiStateOutput",
    "multi-state-value": "multiStateValue", "characterstring-value": "characterstringValue",
}

UNIT = os.environ.get("UNIT_ID", "SIM-ROOM")
DEVICE = int(os.environ.get("DEVICE_INSTANCE", "0"))
# argv overrides env:  pams_ip_poller.py <unit> <device>  (lets the gateway pkill by unit)
import sys as _sys
if len(_sys.argv) > 1:
    UNIT = _sys.argv[1]
if len(_sys.argv) > 2:
    try:
        DEVICE = int(_sys.argv[2])
    except ValueError:
        pass
POINTS_FILE = os.path.expanduser(os.environ.get("PAMS_POINTS_FILE", "~/pams_points.json"))
TEMP_CHANNEL = os.environ.get("TEMP_CHANNEL", "")
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
POLL = float(os.environ.get("POLL_SECONDS", "5"))


def primary_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.1", 1))
        return s.getsockname()[0]
    except Exception:
        return "0.0.0.0"
    finally:
        s.close()


def free_port(ip, ports=(47808, 47809, 47810, 47811, 0)):
    """First bindable UDP port so multiple pollers (and discovery) can coexist."""
    for p in ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind((ip, p))
            actual = s.getsockname()[1]
            s.close()
            return actual
        except OSError:
            s.close()
    return 0


_ip = primary_ip()
_port = free_port(_ip)
BIND = os.environ.get("PAMS_BIP_BIND") or (f"{_ip}/24" if _port == 47808 else f"{_ip}:{_port}")
_found = {}


class Poller(BIPSimpleApplication):
    def do_IAmRequest(self, apdu):
        _found[apdu.iAmDeviceIdentifier[1]] = apdu.pduSource


dev = LocalDeviceObject(objectName="PAMSIPPoller", objectIdentifier=("device", 599010),
                        maxApduLengthAccepted=1024, segmentationSupported="noSegmentation",
                        vendorIdentifier=15)
app = Poller(dev, Address(BIND))
threading.Thread(target=run, daemon=True).start()


def resolve_address(timeout=6):
    req = WhoIsRequest(deviceInstanceRangeLowLimit=DEVICE, deviceInstanceRangeHighLimit=DEVICE)
    req.pduDestination = Address(primary_ip().rsplit(".", 1)[0] + ".255")
    app.request(req)
    end = time.time() + timeout
    while time.time() < end:
        if DEVICE in _found:
            return _found[DEVICE]
        time.sleep(0.2)
    return None


def read_pv(addr, dashed_type, inst):
    ctype = CAMEL.get(dashed_type, dashed_type)
    req = ReadPropertyRequest(objectIdentifier=(ctype, inst), propertyIdentifier="presentValue")
    req.pduDestination = addr
    iocb = IOCB(req)
    app.request_io(iocb)
    iocb.wait(5)
    if iocb.ioError or not iocb.ioResponse:
        return None
    apdu = iocb.ioResponse
    dt = get_datatype(apdu.objectIdentifier[0], apdu.propertyIdentifier)
    if dt is None:
        return None
    try:
        val = apdu.propertyValue.cast_out(dt)
    except Exception:
        return None
    # character-string / text present-value: pass through as a label
    if dashed_type.startswith("characterstring"):
        try:
            return str(val)
        except Exception:
            return None
    try:
        return float(val)
    except (TypeError, ValueError):
        pass
    # binary/enumerated present-value casts to 'active'/'inactive' etc.
    sval = str(val).strip().lower()
    binmap = {"active": 1.0, "inactive": 0.0, "true": 1.0, "false": 0.0, "on": 1.0, "off": 0.0}
    if sval in binmap:
        return binmap[sval]
    try:
        return float(int(val))
    except Exception:
        return None


def load_points():
    try:
        with open(POINTS_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_start()
    print(f"IP poller: unit={UNIT} device={DEVICE} bind={BIND} every {POLL}s -> pams/freezers/{UNIT}", flush=True)

    addr = None
    fails = 0
    while True:
        if addr is None or fails >= 3:
            addr = resolve_address()
            fails = 0
            if addr is None:
                print(f"device {DEVICE} not answering Who-Is; retrying...", flush=True)
                time.sleep(POLL)
                continue
            print(f"device {DEVICE} @ {addr}", flush=True)

        pts = load_points()
        payload = {"unit_id": UNIT, "ts": time.time()}
        for chan, obj in pts.items():
            if ":" not in str(obj):
                continue
            t, i = str(obj).rsplit(":", 1)
            try:
                v = read_pv(addr, t, int(i))
            except Exception:
                v = None
            if v is not None:
                payload[chan] = round(v, 3) if isinstance(v, float) else v

        read_count = len(payload) - 2
        if read_count == 0:
            fails += 1
            time.sleep(POLL)
            continue
        fails = 0

        if "temperature" not in payload:
            tk = TEMP_CHANNEL if TEMP_CHANNEL in payload else next(
                (k for k in payload if k.startswith("temperature")), None)
            if tk:
                payload["temperature"] = payload[tk]

        if "temperature" in payload:
            client.publish(f"pams/freezers/{UNIT}", json.dumps(payload), qos=0)
            print(f"{UNIT}: {read_count} points, temp={payload['temperature']}", flush=True)
        time.sleep(POLL)


if __name__ == "__main__":
    main()
