#!/usr/bin/env python3
"""Sonda experimental: Camoufox (Firefox stealth) contra WAF DataDome de Falabella/Despegar."""
import re
try:
    from camoufox.sync_api import Camoufox
except Exception as e:
    print("probe: camoufox no disponible:", str(e)[:100]); raise SystemExit
URLS = [("falabella", "https://www.viajesfalabella.cl/shop/flights/results/oneway/SCL/RIO/2027-01-22/1/0/0"),
        ("despegar", "https://www.despegar.cl/shop/flights/results/oneway/SCL/RIO/2027-01-22/1/0/0")]
for name, url in URLS:
    try:
        with Camoufox(headless=True, humanize=False) as br:
            pg = br.new_page()
            r = pg.goto(url, timeout=45000, wait_until="domcontentloaded")
            pg.wait_for_timeout(3000)
            html = pg.content()
            prices = re.findall(r"\$\s?\d{2,3}\.\d{3}", html)[:6]
            print(f"PROBE {name}: status={getattr(r,'status','?')} len={len(html)} precios={prices}", flush=True)
    except Exception as e:
        print(f"PROBE {name} ERR: {str(e)[:150]}", flush=True)
