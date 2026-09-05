import paho.mqtt.client as c
print("paho has CallbackAPIVersion:", hasattr(c, "CallbackAPIVersion"))
try:
    print("VERSION2 =", c.CallbackAPIVersion.VERSION2)
except Exception as e:
    print("error:", e)
