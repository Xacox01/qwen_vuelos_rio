#!/usr/bin/env python3
"""Fetcher stealth para Falabella Viajes y Despegar (protegidos por DataDome WAF).

Metodología de ingeniería inversa (rea-style):
  1. Egress por Cloudflare WARP (SOCKS5 via wireproxy, ver warp_boot.sh): DataDome
     puntúa la IP; WARP entrega reputación de consumidor desde GitHub Actions.
  2. Navegador Firefox stealth real (Camoufox) con humanize + geoip.
  3. URL-ONLY-RESULTS: si la SPA redirige fuera de /shop/flights/results/* la lectura
     se descarta completa (evita publicar precios de módulos promocionales de otras
     páginas — la causa raíz de los "precios irreales" reportados).
  4. CAPTURA DE API INTERNA: intercepta las respuestas JSON de búsqueda y extrae el
     mínimo de tarifas del cuerpo (la fuente más fiable: la API responde SOLO con el
     resultado de la consulta exacta ruta/fecha).
  5. GRILLA DE FECHAS (clickedPrice): los enlaces del calendario de la propia OTA
     traen el precio real por fecha; se usa el precio de la fecha consultada o se
     valida el mínimo DOM contra la grilla (≥85% del mínimo de vecinos).
  6. Regla de publicación: api_min > grid_date > dom_min (si pasa sanity). Todo lo
     descartado queda registrado para auditoría.

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

# Escaneo de bajo volumen: 1 ida + 1 vuelta por sitio (DataDome banea por volumen).
# GIG = Aeropuerto Internacional de Galeão (código IATA que el router de Despegar/
# Falabella acepta en deep links; "RIO" provoca redirección a la home).
FAL_DATES = [("SCL", "GIG", "2027-01-22"), ("GIG", "SCL", "2027-02-01")]

# Umbral de plausibilidad: one-way SCL⇄GIG temporada alta (ene-feb 2027).
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

# Respuestas JSON internas de búsqueda (la API responde solo con la consulta exacta)
API_URL_RE = re.compile(r"(search|cluster|itinerar|shopping|results)", re.I)
# campos de tarifa típicos del BFF de Despegar/Falabella
FARE_RE = re.compile(r'"(?:amount|total|price|fare|value)"\s*:\s*"?(\d{5,7})"?')


class ApiCapture:
    """Acumula cuerpos JSON de APIs de búsqueda durante una pata (ida o vuelta)."""

    def __init__(self, max_bodies=60):
        self.bodies = []      # [(url, texto)]
        self.max = max_bodies

    def clear(self):
        self.bodies = []

    def on_response(self, r):
        try:
            ct = (r.headers or {}).get("content-type", "")
            u = r.url or ""
            if "json" not in ct or not API_URL_RE.search(u):
                return
            if len(self.bodies) >= self.max:
                return
            body = r.text()
            if body and len(body) < 3_000_000:
                self.bodies.append((u[:180], body))
        except Exception:
            pass

    def api_prices(self):
        vals = []
        for _, body in self.bodies:
            for m in FARE_RE.finditer(body):
                try:
                    v = int(m.group(1))
                except ValueError:
                    continue
                if PRICE_MIN < v < PRICE_MAX:
                    vals.append(v)
        return vals


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


def decide_price(api_vals, dom_vals, cal, fecha, log, name, tag):
    """Regla de publicación anti-contaminación.

    Prioridad: API interna > grilla (fecha exacta) > DOM validado contra grilla.
    Devuelve (min_clp, base, detalle).
    """
    grid_date = next((c["clp"] for c in cal if c["fecha"] == fecha), None)
    grid_min = min((c["clp"] for c in cal), default=None)
    api_min = min(api_vals) if api_vals else None
    dom_min = min(dom_vals) if dom_vals else None

    det = {"api_min": api_min, "n_api": len(api_vals), "dom_min": dom_min,
           "grid_min": grid_min, "grid_date": grid_date, "n_dom": len(dom_vals),
           "n_grid": len(cal)}

    # 1) API interna: la respuesta corresponde exactamente a esta búsqueda
    if api_min:
        det["base"] = "api"
        return api_min, "api", det

    # 2) grilla de fechas de la propia OTA para la fecha consultada
    if grid_date:
        det["base"] = "grid"
        return grid_date, "grid", det

    # 3) DOM del listado, PERO validado: el mínimo debe ser coherente con la grilla
    #    de vecinos (si está >15% por debajo de TODA la grilla, es un módulo
    #    promocional/otra fecha contaminando → se descarta).
    if dom_min and grid_min and len(cal) >= 4:
        if dom_min >= 0.85 * grid_min:
            det["base"] = "dom_validado"
            return dom_min, "dom_validado", det
        det["contaminacion"] = (f"dom_min {dom_min} < 85% del min de grilla {grid_min} "
                                f"— descartado como promo fuera de contexto")
        log(f"{name} {tag}: CONTAMINACIÓN descartada ({det['contaminacion']})")
        return None, "descartado", det

    # 4) sin grilla de referencia: solo aceptamos DOM si hay ≥6 valores plausibles
    #    (listado real renderizado) — riesgo residual aceptado y registrado.
    if dom_min and len(dom_vals) >= 6:
        det["base"] = "dom_sin_grilla"
        return dom_min, "dom_sin_grilla", det

    det["base"] = "sin_dato"
    return None, "sin_dato", det


def scan_site(pg, name, log, capture):
    dias = []
    for o, d, fecha in FAL_DATES:
        url = f"https://www.{name}/shop/flights/results/oneway/{o}/{d}/{fecha}/1/0/0"
        capture.clear()
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

            url_final = ""
            try:
                url_final = str(pg.url)[:220]
            except Exception:
                pass

            # ── GUARD DE REDIRECCIÓN: solo publicamos lecturas que aterrizan en la
            #    página de resultados de ESTA búsqueda ─────────────────────────────
            req_path = f"/shop/flights/results/oneway/{o}/{d}/{fecha}"
            if req_path not in url_final:
                log(f"{name} {o}->{d} {fecha}: REDIRECCIÓN fuera de resultados "
                    f"({url_final[:90]}) — lectura descartada")
                dias.append({"sentido": f"{o}-{d}", "fecha": fecha, "status": status,
                             "min_clp": None, "calendario": [], "via": VIA,
                             "url_final": url_final,
                             "error": "redirect fuera de /shop/flights/results (precio no verificable)"})
                time.sleep(4.0)
                continue

            html = pg.content()
            try:
                tsec = pg.evaluate("() => document.body ? document.body.innerText.slice(0, 220) : ''").replace("\n", " ")
                log(f"{name} {o}->{d} {fecha}: pg[{pg.title()[:40]}] txt[{tsec[:150]}]")
            except Exception:
                pass

            cal = [{"fecha": m.group(3), "clp": int(m.group(4))}
                   for m in CAL_RE.finditer(html)
                   if PRICE_MIN < int(m.group(4)) < PRICE_MAX]
            # dedupe por fecha conservando el mínimo
            _by = {}
            for c in cal:
                _by[c["fecha"]] = min(_by.get(c["fecha"], 1 << 60), c["clp"])
            cal = [{"fecha": k, "clp": v} for k, v in sorted(_by.items())]

            api_vals = capture.api_prices()
            if api_vals:
                log(f"{name} {o}->{d} {fecha}: API capture {len(capture.bodies)} resp, "
                    f"{len(api_vals)} tarifas, min ${min(api_vals):,}")
            mejor, base, det = decide_price(api_vals, precios, cal, fecha, log, name, f"{o}{d}")

            dias.append({"sentido": f"{o}-{d}", "fecha": fecha, "status": status,
                         "min_clp": mejor, "base": base, "calendario": cal[:24],
                         "n_precios": det.get("n_dom", len(precios)), "via": VIA,
                         "url_final": url_final, **{k: v for k, v in det.items() if k != "base"}})
            if mejor:
                log(f"{name} {o}->{d} {fecha}: ${mejor:,} [{base}] "
                    f"(dom {len(precios)}, grilla {len(cal)}, api {det.get('n_api', 0)})")
            else:
                log(f"{name} {o}->{d} {fecha}: SIN precio verificable ({det.get('base')})")
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
    cap = ApiCapture()
    with Camoufox(**kwargs) as br:
        pg = br.new_page()
        try:
            pg.on("response", cap.on_response)
        except Exception:
            log("API capture no disponible (solo DOM+grilla)")
        out["falabella"] = {"dias": scan_site(pg, "viajesfalabella.cl", log, cap)}
        time.sleep(4)
        out["despegar"] = {"dias": scan_site(pg, "despegar.cl", log, cap)}
    return out


def ok(dias):
    return sum(1 for d in dias if d.get("min_clp"))


if __name__ == "__main__":
    res = fetch_otas()
    for site, data in res.items():
        print(site, "->", ok(data["dias"]), "de", len(data["dias"]), "lecturas con precio")
