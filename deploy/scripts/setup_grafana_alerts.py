#!/usr/bin/env python3
"""Provision PAMS freezer alert rules into Grafana unified alerting.

Idempotent: uses fixed rule UIDs, deletes any existing copy, then recreates.
Runs on the Pi against http://localhost:3000.
Leaves MQTT untouched. Notifications route to the default contact point
(configure SMTP/Slack/webhook in Grafana to actually receive them).
"""
import json
import sys
import urllib.request
import urllib.error

GRAFANA = "http://localhost:3000"
USER = "admin"
PASS = "Noesis1!"
BUCKET = "freezer_data"
FOLDER_UID = "pams-alerts"
FOLDER_TITLE = "PAMS Alerts"
RULE_GROUP = "pams"


def api(method, path, body=None, headers=None):
    url = GRAFANA + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    # basic auth
    import base64
    tok = base64.b64encode(f"{USER}:{PASS}".encode()).decode()
    req.add_header("Authorization", "Basic " + tok)
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def find_influx_uid():
    st, ds = api("GET", "/api/datasources")
    if st != 200:
        print("ERROR listing datasources:", st, ds)
        sys.exit(1)
    influx = [d for d in ds if d.get("type") == "influxdb"]
    if not influx:
        print("ERROR: no influxdb datasource found")
        sys.exit(1)
    # Prefer the one the dashboard uses, then the default, then any.
    for d in influx:
        if d.get("uid") == "influxdb_pams":
            return d["uid"]
    for d in influx:
        if d.get("isDefault"):
            return d["uid"]
    return influx[0]["uid"]


def ensure_folder():
    st, _ = api("POST", "/api/folders", {"uid": FOLDER_UID, "title": FOLDER_TITLE})
    if st in (200, 412, 409, 400):
        print(f"folder '{FOLDER_TITLE}' ready (status {st})")
    else:
        print("folder create status", st)


def flux_last(field, measurement):
    return (
        f'from(bucket: "{BUCKET}") '
        f'|> range(start: -15m) '
        f'|> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "{field}") '
        f'|> last()'
    )


def build_rule(uid, title, field, measurement, op, threshold, for_dur, summary, ds_uid):
    return {
        "uid": uid,
        "title": title,
        "ruleGroup": RULE_GROUP,
        "folderUID": FOLDER_UID,
        "orgID": 1,
        "condition": "C",
        "for": for_dur,
        "noDataState": "OK",
        "execErrState": "OK",
        "annotations": {"summary": summary},
        "labels": {"service": "pams", "severity": "warning"},
        "data": [
            {
                "refId": "A",
                "relativeTimeRange": {"from": 900, "to": 0},
                "datasourceUid": ds_uid,
                "model": {
                    "refId": "A",
                    "datasource": {"type": "influxdb", "uid": ds_uid},
                    "query": flux_last(field, measurement),
                    "intervalMs": 1000,
                    "maxDataPoints": 43200,
                },
            },
            {
                "refId": "B",
                "relativeTimeRange": {"from": 900, "to": 0},
                "datasourceUid": "__expr__",
                "model": {
                    "refId": "B",
                    "type": "reduce",
                    "datasource": {"type": "__expr__", "uid": "__expr__"},
                    "expression": "A",
                    "reducer": "last",
                },
            },
            {
                "refId": "C",
                "relativeTimeRange": {"from": 900, "to": 0},
                "datasourceUid": "__expr__",
                "model": {
                    "refId": "C",
                    "type": "threshold",
                    "datasource": {"type": "__expr__", "uid": "__expr__"},
                    "expression": "B",
                    "conditions": [
                        {"evaluator": {"type": op, "params": [threshold]}}
                    ],
                },
            },
        ],
    }


def upsert_rule(rule):
    uid = rule["uid"]
    api("DELETE", f"/api/v1/provisioning/alert-rules/{uid}")
    st, resp = api(
        "POST",
        "/api/v1/provisioning/alert-rules",
        rule,
        headers={"X-Disable-Provenance": "true"},
    )
    ok = st in (200, 201)
    print(f"  [{'OK' if ok else 'FAIL'}] {rule['title']} (status {st})")
    if not ok:
        print("     ->", resp)
    return ok


def main():
    ds = find_influx_uid()
    print("influxdb datasource uid:", ds)
    ensure_folder()
    rules = [
        build_rule("pams-health-low", "PAMS: Freezer Health Low",
                   "health_score", "ml_scores", "lt", 50, "5m",
                   "ML health score below 50 for 5m", ds),
        build_rule("pams-anomaly", "PAMS: Anomaly Detected",
                   "anomaly", "ml_scores", "gt", 0.5, "2m",
                   "ML anomaly flag raised", ds),
        build_rule("pams-temp-high", "PAMS: Temperature High (TUNE threshold)",
                   "temperature", "readings", "gt", -10, "10m",
                   "Freezer temperature above -10C for 10m (tune to your setpoint)", ds),
        build_rule("pams-door-open", "PAMS: Door Open Too Long",
                   "door_status", "readings", "gt", 0.5, "10m",
                   "Freezer door reported open for 10m", ds),
    ]
    print("provisioning alert rules:")
    results = [upsert_rule(r) for r in rules]
    print("--- alert rules now in Grafana ---")
    st, existing = api("GET", "/api/v1/provisioning/alert-rules")
    if st == 200 and isinstance(existing, list):
        for r in existing:
            print(" -", r.get("title"), "| for", r.get("for"), "| folder", r.get("folderUID"))
    print("ALERTS OK" if all(results) else "SOME RULES FAILED")


if __name__ == "__main__":
    main()
