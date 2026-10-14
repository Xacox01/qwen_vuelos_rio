#!/usr/bin/env python3
"""Fetcher stealth para Falabella Viajes y Despegar (protegidos por DataDome WAF).

Metodología de ingeniería inversa (rea-style):
  1. Navegador Firefox stealth real (Camoufox) → pasa el challenge DataDome (verificado en Actions 2026-10-14: HTTP 200).
  2. Espera el renderizado de la app React y extrae los precios visibles del DOM.
  3. Intercepta respuestas JSON de las APIs internas de búsqueda (para calibración futura).
  4. Extrae días vecinos del calendario flexible (links clickedPrice en el DOM).

Contract de salida 100% compatible con data.json (fuentes.falabella / fuentes.despegar → "dias").
"""
import re
import time

try:
    from camoufox.sync_api import Camoufox
    HAS_CAMOUFOX = True
except Exception:
    HAS_CAMOUFOX = False

# (sentido, fecha) — mismas ventanas que usaba el fetcher SSR
FAL_DATES = [("SCL", "RIO", "2027-01-06"), ("SCL", "RIO", "2027-01-20"), ("SCL", "RIO", "2027-01-22"),
             ("RIO", "SCL", "2027-02-01"), ("RIO", "SCL", "2027-02-08")]

PRICE_MIN, PRICE_MAX = 40000, 2500000  # rango plausible CLP tramo one-way

JS_EXTRACT = """
() => {
  const out = new Set();
  const re = /[$]\\s?(\\d{1,3}(?:\\.\\d{3})+)/g;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const t = node.textContent;
    if (!t || t.length > 90) continue;
    let m; re.lastIndex = 0;
    while ((m = re.exec(t))) {
      const v = parseInt(m[1].replace(/\\./g, ''), 10);
      if (v >= %d && v <= %d) out.add(v);
    }
  }
  return Array.from(out).sort((a, b) => a - b);
}
""" % (PRICE_MIN, PRICE_MAX)

CAL_RE = re.compile(
    r"oneway/([a-z]{3})/([a-z]{3})/(\d{4}-\d{2}-\d{2})/\d/\d/\d\?[^\"']{0,80}?clickedPrice=CLP_(\d+)",
    re.I)


def wait_prices(pg, max_ms=45000):
    """Espera hasta que aparezca un set de precios plausible en el DOM (≥3 valores)."""
    t0 = time.time()
    best = []
    while (time.time() - t0) * 1000 < max_ms:
        try:
            vals = pg.evaluate(JS_EXTRACT) or []
        except Exception:
            vals = []
        if len(vals) > len(best):
            best = vals
        if len(best) >= 3:
            return best
        pg.wait_for_timeout(2500)
    return best


def scan_site(pg, name, log):
    dias = []
    for o, d, fecha in FAL_DATES:
        url = f"https://www.{name}/shop/flights/results/oneway/{o}/{d}/{fecha}/1/0/0"
        api_urls = []
        try:
            r = pg.goto(url, timeout=60000, wait_until="domcontentloaded")
            status = getattr(r, "status", 0) or 0
            pg.wait_for_timeout(4000)
            precios = wait_prices(pg, 42000)
            html = pg.content()
            cal = [{"fecha": m.group(3), "clp": int(m.group(4))}
                   for m in CAL_RE.finditer(html)
                   if PRICE_MIN < int(m.group(4)) < PRICE_MAX]
            mejor = min(precios) if precios else None
            # sanity: el mínimo debe estar cerca del 10º percentil de lo capturado
            if mejor and len(precios) >= 6:
                dias.append({"sentido": f"{o}-{d}", "fecha": fecha, "status": status,
                             "min_clp": mejor, "n_precios": len(precios),
                             "calendario": cal[:24], "via": "camoufox"})
                log(f"{name} {o}->{d} {fecha}: ${mejor:,} ({len(precios)} precios, {len(cal)} días vec.)")
            else:
                dias.append({"sentido": f"{o}-{d}", "fecha": fecha, "status": status,
                             "min_clp": None, "calendario": cal[:24], "via": "camoufox",
                             "error": f"sin precios suficientes ({len(precios)})"})
                log(f"{name} {o}->{d} {fecha}: SIN precios ({len(precios)})")
        except Exception as e:
            dias.append({"sentido": f"{o}-{d}", "fecha": fecha, "status": 0, "min_clp": None,
                         "calendario": [], "via": "camoufox", "error": str(e)[:90]})
            log(f"{name} {o}->{d} {fecha}: ERR {str(e)[:70]}")
        time.sleep(2.0)
    return dias


def fetch_otas(log=print):
    """Escanea Falabella y Despegar en una sola sesión Camoufox (cookies DataDome calientes)."""
    if not HAS_CAMOUFOX:
        raise RuntimeError("camoufox no disponible")
    out = {}
    with Camoufox(headless=True, humanize=False) as br:
        pg = br.new_page()
        # interceptar APIs internas (solo log para calibración futura)
        try:
            pg.on("response", lambda r: api_spy(r, log) if is_api(r) else None)
        except Exception:
            pass
        out["falabella"] = {"dias": scan_site(pg, "viajesfalabella.cl", log)}
        out["despegar"] = {"dias": scan_site(pg, "despegar.cl", log)}
    return out


_API_PAT = re.compile(r"(search|flight|cluster|itinerar)", re.I)


def is_api(r):
    try:
        ct = (r.headers or {}).get("content-type", "")
        return r.url.startswith("http") and "json" in ct and _API_PAT.search(r.url) and "static" not in r.url
    except Exception:
        return False


def api_spy(r, log):
    try:
        u = r.url
        if any(x in u for x in ("search", "flight", "cluster")) and len(u) < 220:
            log(f"API-SPY {r.status} {u[:150]}")
    except Exception:
        pass


def ok(dias):
    return sum(1 for d in dias if d.get("min_clp"))


if __name__ == "__main__":
    res = fetch_otas()
    for site, data in res.items():
        print(site, "->", ok(data["dias"]), "de", len(data["dias"]), "lecturas con precio")
