#!/usr/bin/env python3
"""Diagnóstico v2: espera larga, captura TODAS las APIs JSON, screenshot y texto final."""
import json
import time
from camoufox.sync_api import Camoufox

TARGETS = [
    ("despegar", "https://www.despegar.cl/shop/flights/results/oneway/SCL/RIO/2027-01-22/1/0/0"),
]

out = {}
with Camoufox(headless=True, humanize=True) as br:
    pg = br.new_page()
    cap = []
    def on_resp(r):
        try:
            ct = (r.headers or {}).get("content-type", "")
            if "json" in ct and "despegar" in r.url:
                try:
                    body = r.text()[:800]
                except Exception:
                    body = "(sin cuerpo)"
                cap.append({"s": r.status, "u": r.url[:200], "b": body})
        except Exception:
            pass
    pg.on("response", on_resp)
    r = pg.goto(TARGETS[0][1], timeout=60000, wait_until="domcontentloaded")
    pg.wait_for_timeout(75000)  # espera larga: dejar que el search API termine y renderice
    out["despegar"] = {
        "status": getattr(r, "status", 0),
        "final_url": pg.url[:200],
        "title": pg.title(),
        "body_text": pg.evaluate("() => document.body ? document.body.innerText.slice(0, 2000) : ''"),
        "prices_visible": pg.evaluate("""() => {
            const out = []; const re = /[$]\\s?(\\d{1,3}(?:\\.\\d{3})+)/g;
            const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n;
            while ((n = w.nextNode())) { let m; re.lastIndex = 0;
              while ((m = re.exec(n.textContent))) out.push(m[1]); }
            return out.slice(0, 40); }"""),
        "n_api": len(cap),
        "api_urls": [c["u"] for c in cap],
    }
    pg.screenshot(path="diag2.png")
    # guardar cuerpos de las APIs más relevantes
    interesting = [c for c in cap if any(k in c["u"].lower() for k in ("search", "cluster", "itinerar", "result"))]
    out["search_apis"] = interesting[:10]
    with open("diag2.json", "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("apis:", len(cap), "| search:", len(interesting), "| precios DOM:", len(out["despegar"]["prices_visible"]))
    print("final_url:", out["despegar"]["final_url"])
    for c in interesting[:5]:
        print("SRCH", c["s"], c["u"][:120])
        print("     ", (c["b"] or "")[:200].replace("\n", " "))
