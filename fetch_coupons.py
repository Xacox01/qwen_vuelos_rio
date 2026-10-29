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
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import urljoin

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "es-CL,es;q=0.9,en;q=0.6"})

# tiendas cupon.cl: slug -> nombre comercial visible
# TASK 25: expansión de 5 a 10 tiendas de viaje (Atrápalo, Hoteles.com,
# Booking.com, Avianca, Grupo Xcaret) — cupon.cl las lista en cupon.cl/<slug>.
STORES = [
    ("jetsmart",       "JetSMART"),
    ("latam",          "LATAM"),
    ("despegar-com",   "Despegar"),
    ("cocha",          "Cocha"),
    ("falabella",      "Viajes Falabella"),
    ("atrapalo-chile", "Atrápalo"),
    ("hoteles-com",    "Hoteles.com"),
    ("booking-com",    "Booking.com"),
    ("avianca",       "Avianca"),
    ("grupo-xcaret",   "Grupo Xcaret"),
]

# TASK 25: landing de cada tienda para el CTA "Aplicar en X" — el clickout de
# cupon.cl suele caer en la HOME del comercio (p.ej. Cocha), inútil para
# ingresar un código de vuelo. Con deep_link el botón abre la sección correcta
# (p.ej. cocha.com/vuelos) donde el código sí se puede aplicar.
DEEP_LINKS = {
    "jetsmart":       "https://jetsmart.com/cl/es",
    "latam":          "https://www.latam.com/cl/es/",
    "despegar-com":   "https://www.despegar.cl/vuelos/",
    "cocha":          "https://www.cocha.com/vuelos",
    "falabella":      "https://www.viajesfalabella.cl/",
    "atrapalo-chile": "https://www.atrapalo.cl/",
    "hoteles-com":    "https://www.hoteles.com/",
    "booking-com":    "https://www.booking.com/index.es.html",
    "avianca":        "https://www.avianca.com/cl/es/",
    "grupo-xcaret":   "https://www.grupoxcaret.com/",
}

# cap por tienda (controla el tamaño de data.json con 10 tiendas)
CAP_POR_TIENDA = 12

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
# TASK 25: cupon.cl cambió el formato del monto: ahora puede venir
# '<span class="amount">$ <b>50000</b></span>' (antes '<b>50%</b>'). Se captura
# todo el contenido del span, se le quitan tags y si es un número puro se
# formatea con separador de miles chileno.
AMOUNT_RE = re.compile(r'<span class="amount">\s*(.*?)\s*</span>', re.S)


def parse_amount(raw: str) -> str:
    t = clean(re.sub(r"<[^>]+>", " ", raw or ""))
    if not t:
        return ""
    m = re.fullmatch(r"[\$\s]*([\d.]{1,12})", t)
    if m:
        try:
            return "$" + f"{int(m.group(1).replace('.', '')):,}".replace(",", ".")
        except ValueError:
            pass
    return t
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


# ---------- capa editorial + saneo (oct 2026) ----------
# Los sitios de cupones listan códigos vencidos como "verified" (ej. BCIMACHD de
# septiembre) y mezclan ofertas de precio de vuelos con códigos reales. Aquí:
#   1) detect_expired(): descarta lo que menciona un mes/año/fecha ya pasado
#   2) detect_banco_dias(): etiqueta restricciones de pago (BCI/MACH, Mastercard...)
#      y de día (miércoles, mar-jue, vie-dom) para que el sitio las muestre
#   3) load_seed(): fusiona seed_coupons.json (capa editorial curada de fuentes
#      oficiales y redes sociales). Gana la versión seed en dedup por code.
MONTHS_ES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
             "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
             "noviembre": 11, "diciembre": 12}
BANK_RE = re.compile(r"\b(BCI|MACHBANK|MACH|Tenpo|Mastercard|CMR|Scotiabank|Santander|Ita[uúú]|Banco de Chile|Galicia|Caja Los Andes)\b", re.I)


def _blob(it: dict) -> str:
    return " ".join([it.get("title", ""), it.get("desc", "")] +
                    [f"{k} {v}" for k, v in (it.get("conds") or {}).items()])


def detect_expired(it: dict) -> bool:
    """True si el cupón menciona un mes pasado, una fecha dd/mm/yyyy pasada o un
    'válido hasta' vencido. El patrón 'miércoles de <mes>' de cupon.cl (ej. BCIMACHD
    de septiembre) cae aquí automáticamente."""
    today = datetime.now()
    blob = _blob(it).lower()
    for m in re.finditer(r"\bde\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)\b", blob):
        if MONTHS_ES[m.group(1)] < today.month:
            return True
    for m in re.finditer(r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})\b", blob):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            if datetime(y, mo, d) < today:
                return True
        except ValueError:
            pass
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", blob):
        try:
            if datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))) < today:
                return True
        except ValueError:
            pass
    return False


def detect_banco_dias(it: dict) -> None:
    """Etiqueta it['banco'] e it['dias'] a partir de las condiciones."""
    blob = _blob(it)
    b = BANK_RE.search(blob)
    if b and not it.get("banco"):   # la etiqueta curada de la seed tiene prioridad
        name = b.group(1)
        it["banco"] = name.upper() if name.upper() in ("BCI", "MACH", "MACHBANK") else name
    low = blob.lower()
    if "miércoles" in low or "miercoles" in low:
        it.setdefault("dias", "miércoles")
    elif "martes" in low and "jueves" in low:
        it.setdefault("dias", "mar-jue")
    elif "viernes" in low and "domingo" in low:
        it.setdefault("dias", "vie-dom")


def load_seed() -> list:
    """Carga seed_coupons.json (capa editorial). Descarta la semilla completa si
    lleva más de 45 días sin revisión humana (anti-staleness)."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_coupons.json")
    try:
        with open(p, encoding="utf-8") as fh:
            seed = json.load(fh)
        rev = seed.get("revisado") or ""
        cut = (datetime.now(timezone.utc) - timedelta(days=45)).strftime("%Y-%m-%d")
        if rev and rev < cut:
            print(f"  [seed] obsoleta (revisado {rev} < {cut}) — se ignora", file=sys.stderr)
            return []
        items = []
        for it in seed.get("items", []):
            it = dict(it)
            it["src_editorial"] = True
            items.append(it)
        print(f"  [seed] {len(items)} cupones editoriales (revisado {rev})")
        return items
    except Exception as e:
        print(f"  [seed] no disponible: {e}", file=sys.stderr)
        return []

def fetch_store(store_slug: str):
    """Devuelve (items, updated_label, error) para una tienda de cupon.cl."""
    url = f"https://cupon.cl/{store_slug}"
    r = S.get(url, timeout=25)
    r.raise_for_status()
    h = r.text
    upd = UPD_RE.search(h)
    # TASK 25: cupon.cl eliminó el elemento card-shop-header__updated del HTML;
    # si no aparece, se declara la fecha de revisión del propio fetch para que
    # fuentes_upd nunca vuelva vacío (antes Cocha/Despegar quedaban sin nota).
    upd = clean(upd.group(1)) if upd else f"revisado {datetime.now(timezone.utc).strftime('%d-%m-%Y')} (fetch)"
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
            "amount": parse_amount(amount.group(1)) if amount else "",
            "deep_link": DEEP_LINKS.get(store_slug, ""),
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
            uniq = uniq[:CAP_POR_TIENDA]
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

    # saneo: descartar vencidos (mes/fecha pasada) y etiquetar restricciones
    n_exp = 0
    clean = []
    for it in items:
        if detect_expired(it):
            n_exp += 1
            continue
        detect_banco_dias(it)
        clean.append(it)
    items = clean

    # capa editorial: la seed gana en dedup por code
    have = {x.get("code") for x in items if x.get("code")}
    for s in load_seed():
        if s.get("code") and s["code"] in have:
            continue
        if detect_expired(s):
            n_exp += 1
            continue
        detect_banco_dias(s)
        items.append(s)

    # orden global: códigos primero (conf alta primero), luego beneficios, ofertas al final
    items.sort(key=lambda x: (0 if x.get("code") else (1 if x.get("kind") == "beneficio" else 2),
                              0 if x.get("conf") == "alta" else 1))

    out = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "ok": len(items) > 0,
        "secs": round(time.time() - t0, 1),
        "source": "cupon.cl + seed editorial",
        "stats": {
            "total": len(items),
            "con_codigo": sum(1 for x in items if x["code"]),
            "beneficios": sum(1 for x in items if x.get("kind") == "beneficio"),
            "ofertas": sum(1 for x in items if x.get("kind") == "oferta"),
            "expirados_filtrados": n_exp,
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
          f"({out['stats']['con_codigo']} con código, "
          f"{out['stats']['beneficios']} beneficios, "
          f"{out['stats']['expirados_filtrados']} vencidos filtrados) en {out['secs']}s")
    for it in items[:8]:
        print(f"   · [{'CÓD ' + it['code'] if it['code'] else it['kind']:>14}] "
              f"{it['merchant']:<16} {it['title'][:62]}")

if __name__ == "__main__":
    main()
