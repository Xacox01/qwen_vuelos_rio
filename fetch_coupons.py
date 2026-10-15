#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_coupons.py — Recopilador de cupones y promociones activas (100% serverless).

Fuentes (HTTP plano, sin navegador):
  PRIORITARIA  cupon.cl   (Global Savings Group Chile) — tiendas:
                          jetsmart, latam, despegar-com, cocha, falabella
  SECUNDARIA   radarcupon.cl (mismo grupo/editorial; puede 403 según IP — opcional)

Resolución de códigos:
  Los códigos reales viven en el endpoint AJAX  /modals/coupon_clickout?id=<id>
  y vienen en base64. Se decodifican y se guardan junto al cupón.

Filtrado de relevancia:
  Tiendas de aerolíneas/agencias → se aceptan todos.
  Falabella (retail general) → sólo ítems con keywords de viaje.

Salida:  data.json["cupones"] = {ts, ok, stats, fuentes_upd, items[]}
Diseñado para correr dentro de GitHub Actions (refresh.yml), gate 6 h.
"""
import base64
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "es-CL,es;q=0.9,en;q=0.6"})

# tiendas cupon.cl: slug -> nombre comercial visible
STORES = [
    ("jetsmart",     "JetSMART"),
    ("latam",        "LATAM"),
    ("despegar-com", "Despegar"),
    ("cocha",        "Cocha"),
    ("falabella",    "Viajes Falabella"),
]

# keywords de viaje para filtrar tiendas retail (falabella)
TRAVEL_KW = re.compile(
    r"vuelo|aere|aére|hotel|alojami|paquete|viaje|destino|brasil|caribe|playa|"
    r"crucero|asistencia|equipaje|maleta|arriendo de autos|renta de autos|"
    r"auto[s]? en|turismo|resort|vacacion|aerolinea|aerolínea|búzios|florianó|"
    r"rio|río|cartagena|cancún|punta cana|san andrés|varadero|cosmovision", re.I)

BLOCK_RE = re.compile(
    r'<div[^>]*\bdata-coupon-id=[\"\'](\d+)[\"\']([^>]*)>(.*?)(?=<div[^>]*\bdata-coupon-id=|'
    r'<div class="coupons__pagination|<section|</main)', re.S)
TITLE_RE = re.compile(r"data-coupon-title=['\"]([^'\"]+)['\"]")
TYPE_RE = re.compile(r'data-coupon-type=[\"\'](\w+)[\"\']')
CLICK_RE = re.compile(r"data-coupon-url=['\"]([^'\"]+)['\"]")
AMOUNT_RE = re.compile(r'<span class="amount"><b>([^<]+)</b>([^<]*)</span>')
DESC_RE = re.compile(r'<div class="coupon__description[^"]*"[^>]*>.*?'
                     r'<p>(.*?)</p>', re.S)
TABLE_RE = re.compile(r'<span class="key">([^<]+)</span>\s*'
                      r'<span class="value">([^<]+)</span>', re.S)
VERIF_RE = re.compile(r'coupon__label--verified')
EXPIRE_RE = re.compile(r'[Vv]álid[oa] (?:hasta|hasta el)\s*:?\s*([0-9]{1,2}[°.\-\s/]*[a-zA-ZçÇ]+[^<\n]{0,12}|[0-9]{2,4}[-/][0-9]{1,2}[-/][0-9]{1,4})')
CODE_RE = re.compile(r"<span[^>]*coupon-code[^>]*>\s*([A-Za-z0-9\-_.]{2,30})\s*<")
CODE_B64_RE = re.compile(r'data-coupon-code="([A-Za-z0-9+/=]+)"')
UPD_RE = re.compile(r'<(?:span|div|p)[^>]*card-shop-header__updated[^>]*>([^<]{5,80})')

def clean(t: str) -> str:
    return re.sub(r"\s+", " ", unescape(t or "")).strip()

def b64d(s: str) -> str:
    try:
        return base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", "replace").strip()
    except Exception:
        return ""

def fetch_store(store_slug: str):
    """Devuelve (items, updated_label, error) para una tienda de cupon.cl."""
    url = f"https://cupon.cl/{store_slug}"
    r = S.get(url, timeout=25)
    r.raise_for_status()
    h = r.text
    upd = UPD_RE.search(h)
    upd = clean(upd.group(1)) if upd else ""
    items = []
    for cid, attrs, body in BLOCK_RE.findall(h):
        title = TITLE_RE.search(attrs + body)
        if not title:
            continue
        title = clean(title.group(1))
        ctype = TYPE_RE.search(attrs + body)
        click = CLICK_RE.search(body)
        amount = AMOUNT_RE.search(body)
        desc = DESC_RE.search(body)
        conds = {clean(k): clean(v) for k, v in TABLE_RE.findall(body) if len(clean(v)) < 260}
        verified = bool(VERIF_RE.search(body))
        exp = EXPIRE_RE.search(body)
        items.append({
            "id": cid,
            "store": store_slug,
            "kind": "codigo" if (ctype and ctype.group(1) == "coupon") else "oferta",
            "title": title,
            "amount": clean(f"{amount.group(1)}{amount.group(2)}") if amount else "",
            "desc": clean(desc.group(1))[:300] if desc else "",
            "conds": conds,
            "verified": verified,
            "expires": clean(exp.group(1)) if exp else "",
            "url": urljoin("https://cupon.cl", click.group(1)) if click
                   else f"https://cupon.cl/ir-al-cupon/{cid}",
            "code": None,
        })
    return items, upd, None

def resolve_code(item: dict) -> None:
    """Descarga el modal del cupón y decodifica el código (base64)."""
    try:
        r = S.get("https://cupon.cl/modals/coupon_clickout",
                  params={"id": item["id"]}, timeout=20,
                  headers={"X-Requested-With": "XMLHttpRequest",
                           "Referer": "https://cupon.cl/"})
        if r.status_code != 200:
            return
        m = CODE_RE.search(r.text)
        cand = m.group(1) if m else None
        if not cand:
            m2 = CODE_B64_RE.search(r.text)
            if m2:
                cand = b64d(m2.group(1))
        if cand and re.fullmatch(r"[A-Za-z0-9\-_.]{3,30}", cand):
            item["code"] = cand
    except Exception:
        pass

def main():
    t0 = time.time()
    items, per_store, fails = [], {}, []
    for slug, name in STORES:
        try:
            raw, upd, _ = fetch_store(slug)
            if slug == "falabella":  # retail general → sólo viaje
                raw = [x for x in raw if TRAVEL_KW.search(x["title"] + " " + x["desc"])]
            # dedupe por título normalizado
            seen, uniq = set(), []
            for x in raw:
                k = re.sub(r"[^a-z0-9]", "", x["title"].lower())[:60]
                if k in seen:
                    continue
                seen.add(k)
                x["merchant"] = name
                uniq.append(x)
            uniq.sort(key=lambda x: (0 if x["code"] else (1 if x["verified"] else 2),
                                     x["kind"] == "oferta"))
            uniq = uniq[:16]
            per_store[slug] = {"name": name, "updated": upd,
                               "n": len(uniq), "total": len(raw)}
            items.extend(uniq)
            print(f"  [{slug}] {len(uniq)}/{len(raw)} cupones · {upd}")
        except Exception as e:
            fails.append(f"{slug}: {e}")
            print(f"  [{slug}] FALLO: {e}", file=sys.stderr)

    # resolver códigos reales (sólo kind=codigo sin código aún)
    for it in items:
        if it["kind"] == "codigo" and not it["code"]:
            resolve_code(it)
            time.sleep(0.4)

    out = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "ok": len(items) > 0,
        "secs": round(time.time() - t0, 1),
        "source": "cupon.cl",
        "stats": {
            "total": len(items),
            "con_codigo": sum(1 for x in items if x["code"]),
            "por_tienda": {k: v["n"] for k, v in per_store.items()},
        },
        "fuentes_upd": {k: v["updated"] for k, v in per_store.items()},
        "fails": fails,
        "items": items,
    }

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    prev = data.get("cupones", {})
    if not items and prev.get("items"):
        out["items"] = prev["items"]                 # degradación suave
        out["stats"]["total"] = len(prev["items"])
        out["ok"] = prev.get("ok", False)
        out["stale"] = True
    data["cupones"] = out
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))

    print(f"CUPONES OK: {out['stats']['total']} items "
          f"({out['stats']['con_codigo']} con código) en {out['secs']}s")
    for it in items[:6]:
        print(f"   · [{'CÓD ' + it['code'] if it['code'] else it['kind']:>14}] "
              f"{it['merchant']:<16} {it['title'][:62]}")

if __name__ == "__main__":
    main()
