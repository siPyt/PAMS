# PAMS Raspberry Pi — BACnet MS/TP Node Integration Request

**Prepared for:** Siemens BMS Engineering (Insight / APOGEE / Desigo)
**Prepared by:** PAMS pilot team
**Date:** 2026-09-05
**Purpose:** Add a Raspberry Pi as a **BACnet MS/TP master node** on the freezer
trunk so it can **poll** freezer point data (temperature, door) and **write back**
a calculated health score. This is a read-mostly monitoring node plus one
writable analog value.

---

## 0. Assumption / prerequisite (please confirm)

This integration assumes the freezer controller lives on a **BACnet MS/TP (EIA-485)**
trunk. If the freezer is on a Siemens **P1/P2 FLN (proprietary APOGEE)** trunk, a
BACnet MS/TP node **cannot** join it directly — we would instead need a
BACnet-capable trunk (e.g., Desigo/PXC BACnet) or a P1-to-BACnet gateway.
**Please confirm the freezer trunk is BACnet MS/TP.**

---

## 1. Our device — Raspberry Pi PAMS node (what we are adding)

| Parameter                  | Value                             | Notes                                                                    |
| -------------------------- | --------------------------------- | ------------------------------------------------------------------------ |
| Device Object Name         | `RaspberryPi_PAMS_Node`           | can be renamed to your convention                                        |
| **BACnet Device Instance** | ****\_** (please assign)**        | must be unique across the entire BACnet internetwork; we propose `45000` |
| **MS/TP MAC address**      | ****\_** (please assign, 0–127)** | must be an **unused master** MAC on the freezer trunk; we propose `45`   |
| Node type                  | **MS/TP Master**                  | issues Who-Is / ReadProperty / WriteProperty and passes token            |
| Vendor Identifier          | `15` (pilot)                      | placeholder; can be set to an unregistered/again value if required       |
| Max APDU Length            | `480`                             | MS/TP max is 480 octets                                                  |
| Segmentation               | `no-segmentation`                 | requests are single-APDU                                                 |
| Baud rate                  | **must match trunk (see §2)**     |                                                                          |
| Physical layer             | EIA-485, 2-wire + reference       | FTDI FT232R USB→RS-485 adapter on the Pi                                 |

**Services this node originates (client role):**

- `Who-Is` / reads `I-Am` (device binding)
- `ReadProperty` (poll `present-value` of freezer points)
- `WriteProperty` (write the health-score point)

The node does **not** need to serve data to the BMS; it is a client/poller. It
must simply be tolerated as an additional **master** on the token ring.

---

## 2. MS/TP trunk parameters we need FROM you

| Parameter                                                 | Value (please provide)                                 | Our node must match              |
| --------------------------------------------------------- | ------------------------------------------------------ | -------------------------------- |
| Trunk **baud rate**                                       | ****\_\_\_**** (9600 / 19200 / 38400 / 76800 / 115200) | yes — must match exactly         |
| **Max_Master** on trunk                                   | ****\_\_\_**** (commonly 127)                          | yes — set consistently           |
| Max_Info_Frames                                           | ****\_\_\_**** (commonly 1)                            | we use 1                         |
| Existing **master MAC addresses in use**                  | ****\_\_\_****                                         | so we pick a free MAC for the Pi |
| BACnet **Network Number** of this trunk                   | ****\_\_\_****                                         | needed if behind a BACnet router |
| Is there a BACnet **router/BBMD** in front of this trunk? | Y / N, address ****\_\_\_****                          | for addressing                   |

---

## 3. Freezer controller + points we need to poll

Please provide the target controller identity and the object list.

**Controller identity**

| Parameter                              | Value (please provide) |
| -------------------------------------- | ---------------------- |
| Freezer controller **Device Instance** | ****\_\_\_****         |
| Freezer controller **MS/TP MAC**       | ****\_\_\_**** (0–127) |

**Point list** (BACnet objects the Pi will read/write)

| Function                           | Object Type                                           | Object Instance | Property      | Access    | Units / notes                                               |
| ---------------------------------- | ----------------------------------------------------- | --------------- | ------------- | --------- | ----------------------------------------------------------- |
| Freezer temperature                | Analog Input (0) or Analog Value (2) — please confirm | **\_\_\_**      | present-value | **Read**  | °C or °F? confirm                                           |
| Door status (optional)             | Binary Input (3) or Binary Value (5)                  | **\_\_\_**      | present-value | **Read**  | active = open? confirm                                      |
| **PAMS health score (write-back)** | **Analog Value (2)** preferred                        | **\_\_\_**      | present-value | **Write** | 0–100, dimensionless                                        |
| Write priority for score           | —                                                     | —               | —             | —         | we will write at priority **8** (please confirm acceptable) |

**Write-back requirements:**

- Please provision **one writable Analog Value** dedicated to the PAMS score, or
  confirm an existing writable AV we may use.
- Confirm the point **accepts external WriteProperty** at priority 8 (or specify
  the priority/relinquish behavior you prefer).
- If write-back is not desired for the pilot, we can run **read-only** and expose
  the score elsewhere (MQTT/Grafana) — just let us know.

---

## 4. Physical / wiring

| Item             | Detail                                                                                                                                                                         |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Adapter          | FTDI FT232R USB→RS-485 (chipset verified) on the Pi                                                                                                                            |
| Connections      | **A ↔ A (D+/‑)**, **B ↔ B (D−/+)**, **GND ↔ signal reference/shield**                                                                                                          |
| Bias/termination | Pi adapter is a mid-span node: **no** 120 Ω termination on the Pi unless it is a physical end of the trunk; end-of-line termination + fail-safe bias should exist on the trunk |
| Polarity note    | If no communication, try **swapping A/B** (most common MS/TP wiring issue)                                                                                                     |

---

## 5. What we've already validated on our side

- Pi RS-485 adapter enumerates as `/dev/ttyUSB0` (FTDI FT232R).
- BACnet MS/TP master engine initializes on the port at the configured baud, MAC,
  and max-master (verified: it opens the port, joins as a master, and issues
  Who-Is; currently no devices because wires are not yet landed).
- FT232R USB latency timer set to **1 ms** for reliable MS/TP token timing.
- Once you provide §2/§3, the Pi can bind to the controller (Who-Is/I-Am) and
  begin polling immediately.

---

## 6. Summary of the single ask

1. Confirm the trunk is **BACnet MS/TP**.
2. **Assign** the Pi an unused **MS/TP MAC** and a unique **Device Instance**.
3. Provide the **trunk baud / Max_Master / Network Number**.
4. Provide the freezer controller's **Device Instance + MAC** and the
   **object IDs** for temperature (read), door (read, optional), and a
   **writable Analog Value** for the health score (write, priority 8).

With those, the Pi joins the trunk as a monitoring master and begins polling.
