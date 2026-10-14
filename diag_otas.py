#!/usr/bin/env python3
"""Diagnóstico visual/red de Despegar y Falabella bajo Camoufox.
Genera diag.json (texto visible + APIs capturadas) y diag.png (screenshot)."""
import json
import time
from camoufox.sync_api import Camoufox

TARGETS = [
    ("despegar", "https://www.despegar.cl/shop/flights/results/oneway/SCL/RIO/2027-01-22/1/0/0"),
    ("falabella", "https://www.viajesfalabella.cl/shop/flights/results/oneway/SCL/RIO/2027-01-22/1/0/0"),
]

out = {}
with Camoufox(headless=True, humanize=False, geo={"latitude": -33.45, "longitude": -70.66}) as br:
    pg = br.new_page()
    for name, url in TARGETS:
        cap = []
        def on_resp(r, cap=cap):
            try:
                ct = (r.headers or {}).get("content-type", "")
                if "json" in ct:
                    cap.append({"s": r.status, "u": r.url[:180], "b": r.text()[:500]})
            except Exception:
                pass
        pg.on("response", on_resp)
        try:
            r = pg.goto(url, timeout=60000, wait_until="domcontentloaded")
            pg.wait_for_timeout(50000)
            out[name] = {
                "status": getattr(r, "status", 0),
                "final_url": pg.url[:180],
                "title": pg.title(),
                "body_text": pg.evaluate("() => document.body ? document.body.innerText.slice(0, 1500) : ''"),
                "n_api": len(cap),
                "api": cap[:25],
            }
            pg.screenshot(path=f"diag_{name}.png")
        except Exception as e:
            out[name] = {"error": str(e)[:200]}
        pg.remove_listener("response", on_resp)
        time.sleep(2)

json.dump(out, open("diag.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps({k: {kk: (vv if not isinstance(vv, (list, str)) or kk in ("status", "n_api") else str(vv)[:120]) for kk, vv in v.items()} for k, v in out.items()}, ensure_ascii=False, indent=1)[:2500])
