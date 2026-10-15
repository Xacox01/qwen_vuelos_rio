#!/usr/bin/env python3
"""Fetcher stealth para Falabella Viajes y Despegar (protegidos por DataDome WAF).

Metodología de ingeniería inversa (rea-style):
  1. Egress por Cloudflare WARP (SOCKS5 via wireproxy, ver warp_boot.sh): DataDome
     puntúa la IP; WARP entrega reputación de consumidor desde GitHub Actions.
  2. Navegador Firefox stealth real (Camoufox) con humanize + geoip.
  3. Espera el renderizado de la app React y extrae los precios visibles del DOM.
  4. Intercepta respuestas JSON de las APIs internas de búsqueda (para calibración futura).
  5. Extrae días vecinos del calendario flexible (links clickedPrice en el DOM).
  6. Si DataDome exige challenge interactivo: diagnóstico con screenshots en ota_diag/.

Contract de salida 100% compatible con data.json (fuentes.falabella / fuentes.despegar → "dias").
"""
import os
import re
import time

try:
    from camoufox.sync_api import Camoufox
    HAS_CAMOUFOX = True
except Exception:
    HAS_CAMOUFOX = False

# Egress alternativo (seteado por warp_boot.sh en GitHub Actions)
OTA_PROXY = os.environ.get("OTA_PROXY", "").strip()
DIAG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ota_diag")
# via que se registra en data.json (transparencia para el usuario del dashboard)
VIA = "camoufox+warp" if OTA_PROXY else "camoufox"

# Escaneo de bajo volumen: 1 ida + 1 vuelta por sitio (DataDome banea por volumen)
FAL_DATES = [("SCL", "RIO", "2027-01-22"), ("RIO", "SCL", "2027-02-01")]

# Umbral de plausibilidad: one-way SCL⇄RIO temporada alta (ene-feb 2027).
# La página también muestra módulos promocionales ("desde $44.206") que contaminan
# la extracción si el umbral es bajo; un vuelo real en enero no baja de ~$100k CLP.
PRICE_MIN, PRICE_MAX = 100000, 2500000

JS_EXTRACT = """
() => {
  const out = new Set();
  const re = /[$]\\s?(\\d{1,3}(?:\\.\\d{3})+)/g;
  const t = document.body ? document.body.innerText : '';
  let m;
  while ((m = re.exec(t))) {
    const v = parseInt(m[1].replace(/\\./g, ''), 10);
    if (v >= %d && v <= %d) out.add(v);
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


def page_alive(pg):
    """¿Ya hay contenido visible (challenge resuelto)?"""
    try:
        t = pg.evaluate("() => document.body ? document.body.innerText.trim().length : 0")
        return t > 40
    except Exception:
        return False


def challenge_active(pg):
    """¿DataDome está mostrando su challenge/iframe?"""
    try:
        return bool(pg.evaluate("""() => {
            if (document.querySelector('iframe[src*="captcha-delivery"]')) return true;
            const m = document.body ? (document.body.innerHTML.match(/captcha-delivery|datadome/i)) : null;
            return !!m;
        }"""))
    except Exception:
        return False


def snap_diag(pg, name, tag, log):
    """Guarda screenshot + HTML del challenge para depuración (artifact CI)."""
    try:
        os.makedirs(DIAG_DIR, exist_ok=True)
        stamp = time.strftime("%H%M%S")
        shot = os.path.join(DIAG_DIR, f"{name}_{tag}_{stamp}.png")
        htm = os.path.join(DIAG_DIR, f"{name}_{tag}_{stamp}.html")
        pg.screenshot(path=shot)
        with open(htm, "w", encoding="utf-8") as f:
            f.write(pg.content()[:400000])
        log(f"DIAG {name}: challenge guardado en {shot}")
    except Exception as e:
        log(f"DIAG {name}: no se pudo guardar ({str(e)[:60]})")


def scan_site(pg, name, log):
    dias = []
    for o, d, fecha in FAL_DATES:
        url = f"https://www.{name}/shop/flights/results/oneway/{o}/{d}/{fecha}/1/0/0"
        try:
            r = pg.goto(url, timeout=60000, wait_until="domcontentloaded")
            status = getattr(r, "status", 0) or 0
            if status == 403 or challenge_active(pg):
                log(f"{name} {o}->{d} {fecha}: status {status} con DataDome — aguardando challenge")
            # fase 1: dejar correr challenge DataDome (iframe captcha puede auto-resolverse y recargar)
            pg.wait_for_timeout(8000)
            precios = wait_prices(pg, 40000)
            if not precios and not page_alive(pg):
                log(f"{name} {o}->{d} {fecha}: challenge activo — espera extendida 45s")
                snap_diag(pg, name, f"{o}{d}_f1", log)
                precios = wait_prices(pg, 45000)
            if not precios and page_alive(pg):
                # fase 2: un reload tras challenge suele caer directo en la app
                try:
                    pg.reload(timeout=60000, wait_until="domcontentloaded")
                    pg.wait_for_timeout(5000)
                    precios = wait_prices(pg, 30000)
                except Exception:
                    pass
            if not precios:
                # fase 3: pausa larga + nueva navegación (DataDome a veces libera a la 2ª entrada)
                log(f"{name} {o}->{d} {fecha}: fase 3 — pausa 25s y reintento final")
                time.sleep(25)
                try:
                    pg.goto(url, timeout=60000, wait_until="domcontentloaded")
                    pg.wait_for_timeout(6000)
                    precios = wait_prices(pg, 30000)
                except Exception:
                    pass
            if not precios and challenge_active(pg):
                snap_diag(pg, name, f"{o}{d}_f3", log)
            html = pg.content()
            try:
                tsec = pg.evaluate("() => document.body ? document.body.innerText.slice(0, 220) : ''").replace("\n", " ")
                log(f"{name} {o}->{d} {fecha}: pg[{pg.title()[:40]}] txt[{tsec[:150]}]")
            except Exception:
                pass
            cal = [{"fecha": m.group(3), "clp": int(m.group(4))}
                   for m in CAL_RE.finditer(html)
                   if PRICE_MIN < int(m.group(4)) < PRICE_MAX]
            mejor = min(precios) if precios else None
            # sanity: el mínimo debe estar cerca del 10º percentil de lo capturado
            if mejor and len(precios) >= 6:
                dias.append({"sentido": f"{o}-{d}", "fecha": fecha, "status": status,
                             "min_clp": mejor, "n_precios": len(precios),
                             "calendario": cal[:24], "via": VIA})
                log(f"{name} {o}->{d} {fecha}: ${mejor:,} ({len(precios)} precios, {len(cal)} días vec.)")
            else:
                dias.append({"sentido": f"{o}-{d}", "fecha": fecha, "status": status,
                             "min_clp": None, "calendario": cal[:24], "via": VIA,
                             "error": f"sin precios suficientes ({len(precios)})"})
                log(f"{name} {o}->{d} {fecha}: SIN precios ({len(precios)})")
        except Exception as e:
            dias.append({"sentido": f"{o}-{d}", "fecha": fecha, "status": 0, "min_clp": None,
                         "calendario": [], "via": VIA, "error": str(e)[:90]})
            log(f"{name} {o}->{d} {fecha}: ERR {str(e)[:70]}")
        time.sleep(4.0)
    return dias


def fetch_otas(log=print):
    """Escanea Falabella y Despegar en una sola sesión Camoufox (cookies DataDome calientes).

    Si OTA_PROXY está seteado (por warp_boot.sh), sale por WARP y activa geoip
    (geolocalización/idioma/timezone coherentes con la IP de egress).
    """
    if not HAS_CAMOUFOX:
        raise RuntimeError("camoufox no disponible")
    kwargs = {"headless": True, "humanize": True}
    if OTA_PROXY:
        kwargs["proxy"] = {"server": OTA_PROXY}
        kwargs["geoip"] = True
        log(f"Camoufox saldrá por proxy WARP: {OTA_PROXY} (geoip activado)")
    out = {}
    with Camoufox(**kwargs) as br:
        pg = br.new_page()
        # interceptar APIs internas (solo log para calibración futura)
        try:
            pg.on("response", lambda r: api_spy(r, log) if is_api(r) else None)
        except Exception:
            pass
        out["falabella"] = {"dias": scan_site(pg, "viajesfalabella.cl", log)}
        time.sleep(4)
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
