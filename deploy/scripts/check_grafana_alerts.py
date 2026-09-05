#!/usr/bin/env python3
import json, base64, urllib.request
G="http://localhost:3000"; tok=base64.b64encode(b"admin:Noesis1!").decode()
def get(p):
    r=urllib.request.Request(G+p); r.add_header("Authorization","Basic "+tok)
    return json.load(urllib.request.urlopen(r))
d=get("/api/prometheus/grafana/api/v1/rules")
print("=== alert rule states ===")
for g in d.get("data",{}).get("groups",[]):
    for r in g.get("rules",[]):
        print("%-40s state=%-8s health=%s" % (r.get("name"), r.get("state"), r.get("health")))
