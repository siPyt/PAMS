"""
PAMS BMS Pilot Node - BACnet/IP transport (for laptop/bench testing).

Reads freezer temperature (and optionally door state) from a target BACnet/IP
device, computes the PAMS health score, writes the score back to the BMS, and
publishes the reading to MQTT so it flows into Node-RED -> InfluxDB -> Grafana.

This is the IP twin of the MS/TP node: identical scoring + MQTT logic, only the
BACnet datalink differs. Validate here on BACnet/IP, then deploy the MS/TP
version on the Pi's RS-485 trunk.

Config via environment variables (with sensible defaults):
  BIND_ADDR        This node's BACnet/IP interface, e.g. "192.168.137.226/24"
  TARGET_ADDR      Target device address, e.g. "192.168.137.1:56662"
  TEMP_OBJ         Temperature object, "objtype:instance" (default analogInput:0)
  DOOR_OBJ         Door object (default binaryInput:1); set DOOR_ENABLE=0 to skip
  SCORE_OBJ        Writable score point (default analogValue:2); WRITE_ENABLE=0 to skip
  UNIT_ID          Freezer id for MQTT/tagging (default FRZ-001)
  MQTT_HOST        MQTT broker host (default localhost)
  MQTT_PORT        MQTT broker port (default 1883)
  POLL_SECONDS     Poll interval (default 5)
"""

import os
import json
import time
from threading import Thread

import paho.mqtt.client as mqtt

from bacpypes.core import run, stop
from bacpypes.pdu import Address
from bacpypes.app import BIPSimpleApplication
from bacpypes.local.device import LocalDeviceObject
from bacpypes.object import AnalogValueObject
from bacpypes.primitivedata import Real, Integer, Unsigned, Enumerated
from bacpypes.constructeddata import Any
from bacpypes.apdu import ReadPropertyRequest, WritePropertyRequest, SimpleAckPDU
from bacpypes.iocb import IOCB


# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
def _obj(env_value, default_type, default_instance):
    """Parse 'objtype:instance' from env, else use defaults."""
    if env_value:
        t, i = env_value.split(":")
        return (t, int(i))
    return (default_type, default_instance)


BIND_ADDR = os.environ.get("BIND_ADDR", "192.168.137.226/24")
TARGET_ADDR = os.environ.get("TARGET_ADDR", "192.168.137.1:56662")

TEMP_OBJ = _obj(os.environ.get("TEMP_OBJ"), "analogInput", 0)
DOOR_OBJ = _obj(os.environ.get("DOOR_OBJ"), "binaryInput", 1)
SCORE_OBJ = _obj(os.environ.get("SCORE_OBJ"), "analogValue", 2)

DOOR_ENABLE = os.environ.get("DOOR_ENABLE", "1") == "1"
WRITE_ENABLE = os.environ.get("WRITE_ENABLE", "1") == "1"

UNIT_ID = os.environ.get("UNIT_ID", "FRZ-001")
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "5"))

DEVICE_ID = int(os.environ.get("DEVICE_ID", "45001"))
TOPIC = f"pams/freezers/{UNIT_ID}"


# ----------------------------------------------------------------------------
# MQTT
# ----------------------------------------------------------------------------
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
mqtt_client.loop_start()


# ----------------------------------------------------------------------------
# BACnet application
# ----------------------------------------------------------------------------
this_device = LocalDeviceObject(
    objectName="RaspberryPi_PAMS_BMS_Node",
    objectIdentifier=("device", DEVICE_ID),
    maxApduLengthAccepted=1024,
    segmentationSupported="segmentedBoth",
    vendorIdentifier=15,
)
app = BIPSimpleApplication(this_device, Address(BIND_ADDR))


def read_property(obj_id, prop="presentValue"):
    """Blocking BACnet ReadProperty. Returns the raw propertyValue APDU part or None."""
    req = ReadPropertyRequest(objectIdentifier=obj_id, propertyIdentifier=prop)
    req.pduDestination = Address(TARGET_ADDR)
    iocb = IOCB(req)
    app.request_io(iocb)
    iocb.wait()
    if iocb.ioResponse:
        return iocb.ioResponse.propertyValue
    if iocb.ioError:
        print(f"  ReadProperty {obj_id} error: {iocb.ioError}")
    return None


def read_real(obj_id):
    pv = read_property(obj_id)
    if pv is None:
        return None
    return pv.cast_out(Real)


def read_binary(obj_id):
    """Read a binary/enumerated present value as 0/1. Robust to type."""
    pv = read_property(obj_id)
    if pv is None:
        return None
    for caster in (Enumerated, Unsigned, Integer):
        try:
            return int(pv.cast_out(caster))
        except Exception:
            continue
    return None


def write_score(obj_id, value):
    """Blocking BACnet WriteProperty of a Real presentValue. Returns True on ack."""
    req = WritePropertyRequest(objectIdentifier=obj_id, propertyIdentifier="presentValue")
    req.pduDestination = Address(TARGET_ADDR)
    req.propertyValue = Any()
    req.propertyValue.cast_in(Real(value))
    # priority 8 is a common writable priority; comment out if the point rejects it
    # req.priority = 8
    iocb = IOCB(req)
    app.request_io(iocb)
    iocb.wait()
    if iocb.ioResponse and isinstance(iocb.ioResponse, SimpleAckPDU):
        return True
    if iocb.ioError:
        print(f"  WriteProperty {obj_id} error: {iocb.ioError}")
    return False


def compute_score(live_temp, door_open):
    score = 100.0
    if live_temp > -18.0:
        score -= (live_temp + 18.0) * 15
    if door_open:
        score -= 20
    return max(0.0, min(100.0, score))


def poll_once():
    live_temp = read_real(TEMP_OBJ)
    if live_temp is None:
        print("Skipping cycle: no temperature reading.")
        return

    door_open = False
    if DOOR_ENABLE:
        d = read_binary(DOOR_OBJ)
        if d is not None:
            door_open = (d == 1)

    score = compute_score(live_temp, door_open)

    wrote = False
    if WRITE_ENABLE:
        wrote = write_score(SCORE_OBJ, score)

    payload = {
        "unit_id": UNIT_ID,
        "temperature": float(round(live_temp, 2)),
        "door_status": 1 if door_open else 0,
        "health_score": float(round(score, 1)),
        "ts": time.time(),
    }
    mqtt_client.publish(TOPIC, json.dumps(payload), qos=0)

    print(
        f"{UNIT_ID}  temp={round(live_temp, 2)}C  door={'OPEN' if door_open else 'closed'}  "
        f"score={round(score, 1)}  write={'ok' if wrote else ('skip' if not WRITE_ENABLE else 'FAIL')}  "
        f"-> {TOPIC}"
    )


def polling_loop():
    time.sleep(2)
    while True:
        try:
            poll_once()
        except Exception as e:
            print(f"Poll error: {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    print(f"PAMS BMS Node (BACnet/IP) on {BIND_ADDR}")
    print(f"  target : {TARGET_ADDR}")
    print(f"  temp   : {TEMP_OBJ}")
    print(f"  door   : {DOOR_OBJ if DOOR_ENABLE else 'disabled'}")
    print(f"  score  : {SCORE_OBJ if WRITE_ENABLE else 'write disabled'}")
    print(f"  mqtt   : {MQTT_HOST}:{MQTT_PORT} topic={TOPIC}")

    t = Thread(target=polling_loop, daemon=True)
    t.start()
    try:
        run()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        stop()
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
