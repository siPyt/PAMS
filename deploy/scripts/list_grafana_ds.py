#!/usr/bin/env python3
import json, base64, urllib.request
G="http://localhost:3000"; tok=base64.b64encode(b"admin:Noesis1!").decode()
def get(p):
    r=urllib.request.Request(G+p); r.add_header("Authorization","Basic "+tok)
    return json.load(urllib.request.urlopen(r))
print("=== datasources ===")
for d in get("/api/datasources"):
    print("uid=%s type=%s name=%s isDefault=%s url=%s" % (d["uid"], d["type"], d["name"], d.get("isDefault"), d.get("url")))
