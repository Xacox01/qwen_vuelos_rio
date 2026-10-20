#!/usr/bin/env python3
"""Scraper de GitHub Actions: SCL⇄Río ene–feb 2027.
Consulta fuentes oficiales (JetSMART availability API, SKY Sputnik),
referencias (Falabella SSR, espejo Google/LATAM) y escribe data.json
SOLO si los precios clave cambiaron (para no inflar el historial git).
"""
import json, os, re, sys, time, datetime, traceback, csv

BASE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.join(BASE, "seed")
DATA = os.path.join(BASE, "data.json")
SKY_KEY = "HeQpRjsFI5xlAaSx2onkjc1HTK0ukqA1IrVvd5fvaMhNtzLTxInTpeYB1MK93pah"
SKY_HIST = "https://openair-california.airtrfx.com/airfare-sputnik-service/v3/h2/fares/histogram-distribution"
JS_AVAIL = "https://origin.jsrtff.it.jetsm.art/availability/plain"
WIN_LO, WIN_HI = "2027-01-01", "2027-02-28"
RET_LIMIT = "2027-02-23"
CARNAVAL = ("2027-02-04", "2027-02-13")

try:
    from curl_cffi import requests as cr
    HAS_CURL = True
except Exception:
    HAS_CURL = False

def now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ---------------- JetSMART (API oficial) ----------------
def fetch_jetsmart():
    t0 = time.time()
    r = cr.get(JS_AVAIL, impersonate="chrome", timeout=180)
    r.raise_for_status()
    data = r.json()
    recs = data.get("availability", data) if isinstance(data, dict) else data
    idas, vues = {}, {}
    for rec in recs:
        for leg in (rec, rec.get("rfb")):
            if not isinstance(leg, dict) or leg.get("ft") != "internacional":
                continue
            dep, arr = leg.get("dep"), leg.get("arr")
            if (dep, arr) not in (("SCL", "GIG"), ("GIG", "SCL")):
                continue
            dt = leg.get("date", "")
            fecha, hora = dt[:10], dt[11:16]
            if not (WIN_LO <= fecha <= "2027-03-05"):
                continue
            vuelo = "JA" + str(leg.get("fn", ""))
            clp = (leg.get("pi") or {}).get("clp")
            bucket = leg.get("c", "?")
            if not clp:
                continue
            tgt = idas if (dep, arr) == ("SCL", "GIG") else vues
            key = (fecha, vuelo, hora)
            if key in tgt:
                if clp < tgt[key]["clp"]:
                    tgt[key]["clp"] = clp
                tgt[key]["bk"].append([clp, int(leg.get("s", 0) or 0), bucket])
            else:
                tgt[key] = {"fecha": fecha, "vuelo": vuelo, "hora": hora,
                            "clp": clp, "buckets": [bucket],
                            "bk": [[clp, int(leg.get("s", 0) or 0), bucket]]}
    def flat(d):
        for v in d.values():
            v["bk"].sort(key=lambda b: b[0])
        return sorted(d.values(), key=lambda x: (x["fecha"], x["hora"]))
    I, V = flat(idas), flat(vues)
    return {"idas": I, "vueltas": V,
            "combos": best_combos(I, V, 1),
            "combos_by_pax": {str(n): best_combos(I, V, n) for n in (1, 2, 4, 5, 9)},
            "secs": round(time.time() - t0, 1), "n_records": len(recs)}

def alloc(bk, n):
    """Costo total de n asientos en el mismo vuelo, asignando cubos de menor a mayor precio."""
    tot, rem = 0, n
    for clp, seats, _code in bk:
        take = min(seats, rem)
        if take <= 0:
            continue
        tot += take * clp
        rem -= take
        if rem == 0:
            break
    if rem > 0:
        return None
    return tot

def best_combos(idas, vues, n=1):
    vby = {}
    for v in vues:
        if v["fecha"] <= RET_LIMIT:
            cost = alloc(v.get("bk") or [[v["clp"], 9, "?"]], n)
            if cost is None:
                continue
            vby.setdefault(v["fecha"], []).append((cost, v))
    for k in vby:
        vby[k].sort(key=lambda r: r[0])
    combos = []
    for i in idas:
        if not ("2027-01-05" <= i["fecha"] <= "2027-02-12"):
            continue
        ci = alloc(i.get("bk") or [[i["clp"], 9, "?"]], n)
        if ci is None:
            continue
        d0 = datetime.date.fromisoformat(i["fecha"])
        for stay in range(9, 13):
            rd = (d0 + datetime.timedelta(days=stay)).isoformat()
            if rd > RET_LIMIT or (CARNAVAL[0] <= rd <= CARNAVAL[1]):
                continue
            if rd in vby:
                cv, v = vby[rd][0]
                tot = ci + cv
                combos.append({"ida": i["fecha"], "vuelta": rd, "noches": stay,
                               "v_ida": f"{i['vuelo']} {i['hora']}", "v_vue": f"{v['vuelo']} {v['hora']}",
                               "clp": round(tot / n), "tot": tot})
    combos.sort(key=lambda c: c["tot"])
    top, seen = [], set()
    for c in combos:
        k = (c["ida"], c["vuelta"])
        if k not in seen:
            seen.add(k)
            top.append(c)
        if len(top) >= 20:
            break
    return top

# ---------------- SKY (API oficial Sputnik) ----------------
def fetch_sky():
    t0 = time.time()
    b = {"autoSettings": {"language": "es", "market": "cl"},
         "origin": "SCL", "destination": "GIG", "journeyType": "ROUND_TRIP",
         "departure": {"start": "2027-01-01", "end": "2027-02-14"},
         "tripDuration": {"minimum": 9, "maximum": 12},
         "interval": "1d", "faresLimit": 8,
         "priceBuckets": {"active": True, "field": "TOTAL_PRICE"}}
    H = {"em-api-key": SKY_KEY, "Content-Type": "application/json",
         "Origin": "https://www.skyairline.com", "Referer": "https://www.skyairline.com/"}
    r = cr.post(SKY_HIST, headers=H, json=b, impersonate="chrome", timeout=60)
    r.raise_for_status()
    pairs = []
    for day in r.json().get("histogram", []):
        for f in day.get("fares", []):
            pairs.append({"ida": f["departureDate"], "vuelta": f["returnDate"],
                          "noches": f.get("flightDeltaDays"),
                          "clp": f["priceSpecification"]["totalPrice"]})
    pairs.sort(key=lambda p: p["clp"])
    return {"pairs": pairs, "secs": round(time.time() - t0, 1)}

# ---------------- Falabella (referencia cacheada SSR) ----------------
# GIG = código IATA del aeropuerto de Galeão: "RIO" redirige a la home del router.
FAL_DATES = [("SCL", "GIG", d) for d in ("2027-01-06", "2027-01-20", "2027-01-22")] + \
            [("GIG", "SCL", d) for d in ("2027-02-01", "2027-02-08")]

def fetch_falabella():
    rows = []
    for o, d, fecha in FAL_DATES:
        try:
            u = f"https://www.viajesfalabella.cl/shop/flights/results/oneway/{o}/{d}/{fecha}/1/0/0"
            r = cr.get(u, impersonate="chrome", timeout=45,
                       headers={"Accept-Language": "es-CL,es;q=0.9"})
            h = r.text
            # Guard de redirección: si la URL final no es la de esta búsqueda, el HTML
            # pertenece a otra página (home/challenge) y TODO precio es incontable.
            if f"/shop/flights/results/oneway/{o}/{d}/{fecha}" not in str(getattr(r, "url", "")):
                rows.append({"sentido": f"{o}-{d}", "fecha": fecha, "status": r.status_code,
                             "min_clp": None, "calendario": [],
                             "error": "redirect fuera de resultados (SSR)"})
                continue
            # SOLO grilla de fechas (clickedPrice): los precios de tarjetas/texto
            # incluyen módulos promocionales de otras fechas y contaminan el mínimo.
            cal = []
            for m in re.finditer(r"oneway/[a-z]{3}/[a-z]{3}/(\d{4}-\d{2}-\d{2})/\d/\d/\d\?refererEvent=calendarPricesMatrix&clickedPrice=CLP_(\d+)", h):
                p = int(m.group(2))
                if 100000 < p < 2500000:
                    cal.append({"fecha": m.group(1), "clp": p})
            _by = {}
            for c in cal:
                _by[c["fecha"]] = min(_by.get(c["fecha"], 1 << 60), c["clp"])
            cal = [{"fecha": k, "clp": v} for k, v in sorted(_by.items())]
            exacto = next((c["clp"] for c in cal if c["fecha"] == fecha), None)
            rows.append({"sentido": f"{o}-{d}", "fecha": fecha, "status": r.status_code,
                         "min_clp": exacto, "base": "grid" if exacto else None,
                         "calendario": cal[:24]})
        except Exception as e:
            rows.append({"sentido": f"{o}-{d}", "fecha": fecha, "status": "ERR", "min_clp": None,
                         "calendario": [], "error": str(e)[:80]})
    return {"dias": rows}

# ---------------- Google espejo (LATAM) ----------------
_gf = {}
def _google():
    if "build_tfs" not in _gf:
        src = open(os.path.join(BASE, "google_flights", "core.py"), encoding="utf-8").read()
        src = src.replace("from curl_cffi import requests as creq", "creq=None")
        ns = {}
        exec(compile(src, "core.py", "exec"), ns)
        _gf["build_tfs"] = ns["build_tfs"]
        _gf["Flights"] = ns["Flights"]
    return _gf

def glink(dep, ret):
    t = _google()["build_tfs"]([(dep, "SCL", "GIG"), (ret, "GIG", "SCL")],
                               seat="economy", trip="round_trip", max_stops=0)
    return "https://www.google.com/travel/flights?tfs=" + t + "&hl=es&gl=cl&curr=CLP"

GF_WINDOWS = [("2027-01-13", "2027-01-22"), ("2027-01-22", "2027-02-01"),
              ("2027-01-20", "2027-01-29"), ("2027-02-08", "2027-02-17")]

def fetch_google(cycle):
    dep, ret = GF_WINDOWS[cycle % len(GF_WINDOWS)]
    flights, ins, url = _google()["Flights"]().search(
        "SCL", "GIG", dep, return_date=ret, seat="economy",
        max_stops=0, currency="CLP", language="es", country="cl")
    rows = []
    for f in flights:
        if f.get("price") and f.get("stops") == 0:
            leg = f["legs"][0]
            rows.append({"aerolinea": (f["airlines"] or ["?"])[0], "codigo": leg.get("flightNumber"),
                         "clp": f["price"], "hora": (leg.get("departure") or "")[11:16],
                         "avion": leg.get("aircraft")})
    return {"ventana": [dep, ret], "vuelos": rows, "link": glink(dep, ret)}

# ---------------- LATAM (BFF oficial web) ----------------
import uuid as _uuid
def _latam_headers():
    app = "xp-web-products-searchbox-lib"
    return {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "x-latam-app-session-id": str(_uuid.uuid1()), "x-latam-application-country": "CL",
            "x-latam-application-lang": "es", "x-latam-application-name": app,
            "x-latam-application-oc": "cl", "x-latam-client-name": app,
            "x-latam-request-id": str(_uuid.uuid1()), "x-latam-track-id": str(_uuid.uuid1()),
            "x-latam-action-name": "home-homepage-web-customer.searchbox_calendar.load",
            "Accept": "application/json"}

def _latam_cal(o, d):
    dias = {}
    for m in (1, 2):
        u = (f"https://www.latamairlines.com/bff/web-products-searchbox/v1/calendar?"
             f"origin={o}&destination={d}&month={m}&year=2027&isRoundTrip=false&extended=true&directFlights=true")
        r = cr.get(u, impersonate="chrome", timeout=30, headers=_latam_headers())
        r.raise_for_status()
        for day in r.json().get("days", []):
            for calblk in day.get("calendar", []):
                for det in (calblk.get("detailsCalendar") or []):
                    if det.get("enabled") and det.get("fare"):
                        dias[det["date"]] = {"clp": det["fare"]["amount"],
                                             "directo": bool(det.get("highlightDirectFlight")),
                                             "low": bool(det.get("lowPrice"))}
    return dias

def fetch_latam():
    t0 = time.time()
    out = _latam_cal("SCL", "GIG")
    inn = _latam_cal("GIG", "SCL")
    combos = []
    for ida, vo in out.items():
        if not ("2027-01-05" <= ida <= "2027-02-12"):
            continue
        d0 = datetime.date.fromisoformat(ida)
        for stay in range(9, 13):
            rd = (d0 + datetime.timedelta(days=stay)).isoformat()
            if rd > RET_LIMIT or (CARNAVAL[0] <= rd <= CARNAVAL[1]) or rd not in inn:
                continue
            combos.append({"ida": ida, "vuelta": rd, "noches": stay,
                           "clp": vo["clp"] + inn[rd]["clp"],
                           "directo": vo["directo"] and inn[rd]["directo"]})
    combos.sort(key=lambda c: c["clp"])
    top, seen = [], set()
    for c in combos:
        k = (c["ida"], c["vuelta"])
        if k not in seen:
            seen.add(k)
            top.append(c)
        if len(top) >= 20:
            break
    return {"dias_out": [{"fecha": k, **v} for k, v in sorted(out.items())],
            "dias_in": [{"fecha": k, **v} for k, v in sorted(inn.items())],
            "combos": top, "secs": round(time.time() - t0, 1)}

# ---------------- Despegar (mismo motor SSR que Falabella) ----------------
def fetch_despegar():
    rows = []
    for o, d, fecha in FAL_DATES:
        u = f"https://www.despegar.cl/shop/flights/results/oneway/{o}/{d}/{fecha}/1/0/0"
        r = cr.get(u, impersonate="chrome", timeout=45, headers={"Accept-Language": "es-CL,es;q=0.9"})
        if f"/shop/flights/results/oneway/{o}/{d}/{fecha}" not in str(getattr(r, "url", "")):
            rows.append({"sentido": f"{o}-{d}", "fecha": fecha, "status": r.status_code,
                         "min_clp": None, "calendario": [],
                         "error": "redirect fuera de resultados (SSR)"})
            continue
        h = r.text
        # SOLO grilla de fechas (clickedPrice), sin tarjetas promocionales
        cal = [{"fecha": m.group(1), "clp": int(m.group(2))} for m in re.finditer(
            r"oneway/[a-z]{3}/[a-z]{3}/(\d{4}-\d{2}-\d{2})/\d/\d/\d\?refererEvent=calendarPricesMatrix&clickedPrice=CLP_(\d+)", h)
            if 100000 < int(m.group(2)) < 2500000]
        _by = {}
        for c in cal:
            _by[c["fecha"]] = min(_by.get(c["fecha"], 1 << 60), c["clp"])
        cal = [{"fecha": k, "clp": v} for k, v in sorted(_by.items())]
        exacto = next((c["clp"] for c in cal if c["fecha"] == fecha), None)
        rows.append({"sentido": f"{o}-{d}", "fecha": fecha, "status": 200,
                     "min_clp": exacto, "base": "grid" if exacto else None,
                     "calendario": cal[:24]})
    return {"dias": rows}

# ---------------- Cocha (SSR /vuelos: tarjetas "Desde") ----------------
def fetch_cocha():
    r = cr.get("https://www.cocha.com/vuelos", impersonate="chrome", timeout=30,
               headers={"Accept-Language": "es-CL,es;q=0.9"})
    r.raise_for_status()
    h = r.text
    cards = []
    for blk in re.split(r"Vuelo ida y vuelta", h)[1:]:
        seg = blk[:2600]
        p = re.search(r"Desde\s+\$([\d.]+)", seg)
        alts = re.findall(r'alt="([^"]+)"', seg)
        if p:
            cards.append({"destino": (alts[0] if alts else "?")[:80], "clp": int(p.group(1).replace(".", ""))})
    rio = next((c for c in cards if "Río de Janeiro" in c["destino"]), None)
    return {"rio": rio, "cards": cards[:14]}

# ---------------- Cocha REAL (API GraphQL descubierta) ----------------
COCHA_GQL = "https://apis-prod.cocha.cloud/api-search-manager/graphql"
COCHA_Q = ("query funcion($channel: Channel!, $searchType: SearchType!, $cabinType: CabinType!, "
           "$itinerary: [Itinerary]!, $classType: ClassType!, $maxStops: Int, $fareType: FareType!, "
           "$passengers: String!) { flightV2(channel:$channel, searchType:$searchType, cabinType:$cabinType, "
           "itinerary:$itinerary, classType:$classType, maxStops:$maxStops, fareType:$fareType, "
           "passengers:$passengers) { found{ supplier sections prices{ details total } } } }")
AER = {"H2": "SKY", "JA": "JetSMART", "LA": "LATAM", "G3": "GOL", "AR": "Aerolíneas Argentinas",
       "CM": "Copa", "AV": "Avianca", "IB": "Iberia", "AM": "Aeroméxico", "DL": "Delta",
       "UA": "United", "EK": "Emirates", "LU": "LATAM", "H2": "SKY"}
COCHA_VENTANAS = [("2027-01-02", "2027-02-01"), ("2027-01-22", "2027-02-01"), ("2027-01-10", "2027-01-20")]
_cocha_last = [0.0]

_cocha_sess_cache = {"init": False, "sess": None}

def cocha_sess():
    """Sesión para Cocha: con el túnel WARP disponible (OTA_PROXY, lo levanta
    warp_boot.sh en Actions) sale por esa IP de consumidor — el WAF de Cocha
    rechaza con 403 las IPs de datacenter del runner de GitHub Actions.
    Se resuelve una sola vez por proceso (WARP se levanta antes del scraper)."""
    if _cocha_sess_cache["init"]:
        return _cocha_sess_cache["sess"]
    _cocha_sess_cache["init"] = True
    p = (os.environ.get("OTA_PROXY") or "").strip()
    if p and HAS_CURL:
        # wireproxy (WARP) requiere resolución DNS remota: socks5h (el propio
        # handshake del boot usa --socks5-hostname). Con socks5 plano libcurl
        # resuelve local y el pipeline del proxy falla con curl (97).
        ph = p.replace("socks5://", "socks5h://", 1)
        for cand in ([ph] if ph == p else [ph, p]):
            for kwargs in ({"impersonate": "chrome", "proxy": cand},
                           {"impersonate": "chrome", "proxies": {"http": cand, "https": cand}}):
                try:
                    _cocha_sess_cache["sess"] = cr.Session(**kwargs)
                    print("cocha: sesión vía WARP", cand, flush=True)
                    return _cocha_sess_cache["sess"]
                except Exception:
                    continue
    _cocha_sess_cache["sess"] = None
    return None

def cocha_rt(ida, vuelta, dst="GIG"):
    gap = time.time() - _cocha_last[0]
    if gap < 2.5:
        time.sleep(2.5 - gap)
    variables = {"channel": "B2C", "searchType": "roundtrip", "cabinType": "Y",
                 "itinerary": [{"origin": "SCL", "destination": dst, "date": ida},
                               {"origin": dst, "destination": "SCL", "date": vuelta}],
                 "classType": "EC", "fareType": "P", "passengers": "1", "maxStops": 5}
    # intento 1 por WARP (IP consumidor, pasa el WAF), intento 2 directo
    rutas = []
    _w = cocha_sess()
    if _w is not None:
        rutas.append(("warp", _w))
    rutas.append(("directo", cr))
    r = None
    last_err = None
    for nombre, ses in rutas:
        try:
            r = ses.post(COCHA_GQL, impersonate="chrome", timeout=90,
                        headers={"Content-Type": "application/json", "Origin": "https://www.cocha.com",
                                 "Referer": "https://www.cocha.com/"},
                        json={"operationName": "funcion", "variables": variables, "query": COCHA_Q})
            r.raise_for_status()
            break
        except Exception as e:
            last_err = e
            r = None
            print(f"cocha {dst}: {str(e)[:120]} [{nombre}] — cambio de ruta", flush=True)
            if "429" in str(e):
                time.sleep(20)
            else:
                time.sleep(4)
            continue
    if r is None:
        raise last_err or RuntimeError("cocha sin respuesta")
    _cocha_last[0] = time.time()
    found = ((r.json().get("data") or {}).get("flightV2") or {}).get("found") or []
    out = []
    for f in found:
        try:
            secs = f.get("sections") or []
            if len(secs) < 2:
                continue
            seg_ida, seg_vue = secs[0], secs[1]
            cod = seg_ida[0].get("airline", "?")
            escalas = sum(len(s) - 1 for s in (seg_ida, seg_vue))
            dep_i = str(seg_ida[0].get("departure", ""))
            dep_v = str(seg_vue[0].get("departure", ""))
            hora = dep_i[-5:]
            hvue = dep_v[-5:]
            # auditoría de realidad: la API puede devolver un itinerario de
            # OTRA fecha (tarifa flexible). Guardamos las fechas reales para
            # publicar solo el precio de la fecha pedida y auditable.
            f_i, f_v = dep_i[:10], dep_v[:10]
            precios = []
            pr = f.get("prices")
            items = pr if isinstance(pr, list) else ([pr] if pr else [])
            for p in items:
                if not isinstance(p, dict):
                    continue
                t = p.get("total")
                if isinstance(t, (int, float)):
                    precios.append(t)
                elif isinstance(t, dict):
                    c = (t.get("clp") or {}).get("total")
                    if isinstance(c, (int, float)):
                        precios.append(c)
            if not precios:
                continue
            out.append({"aerolinea": AER.get(cod, cod), "cod": cod,
                        "det": f"{cod}{seg_ida[0].get('connections',[{}])[0].get('flightNumber','')} {hora} salida · vuelta {hvue}",
                        "escalas": escalas, "clp": min(precios),
                        "fecha_ida": f_i, "fecha_vuelta": f_v})
        except Exception:
            continue
    out.sort(key=lambda x: x["clp"])
    return out

COCHA_BRASIL = [("FLN", "Florianópolis"), ("GRU", "São Paulo"), ("SSA", "Salvador"),
                ("CNF", "Belo Horizonte"), ("CWB", "Curitiba"), ("FOR", "Fortaleza"), ("REC", "Recife")]

def fetch_cocha_live(prev=None):
    ventanas = []
    for ida, vuelta in COCHA_VENTANAS:
        res = cocha_rt(ida, vuelta)
        if not res:
            continue
        top = res[:8]
        extras = []
        vistos = {r["det"] for r in top}
        for r in res:
            if r["aerolinea"] not in {x["aerolinea"] for x in top + extras} and r["det"] not in vistos:
                extras.append(r)
            if len(extras) >= 5:
                break
        ventanas.append({"ida": ida, "vuelta": vuelta, "min_clp": res[0]["clp"],
                         "link": f"https://www.cocha.com/resultado/vuelos/SCL/GIG/{ida}/1/Y?origin2=GIG&destination2=SCL&date2={vuelta}",
                         "resultados": top + extras})
    if not ventanas:
        raise RuntimeError("sin resultados cocha")
    # destinos alternativos en Brasil (misma ventana más barata de Río)
    base = min(ventanas, key=lambda v: v["min_clp"])
    rio_min = base["min_clp"]
    brasil_prev = {b["cod"]: b for b in ((prev or {}).get("brasil") or []) if isinstance(b, dict)}
    orden = ([c for c, _ in COCHA_BRASIL if c not in brasil_prev] or [c for c, _ in COCHA_BRASIL])
    lote = orden[:3]
    brasil = [b for c, b in brasil_prev.items() if c not in lote]
    for cod, nombre in [(c, n) for c, n in COCHA_BRASIL if c in lote]:
        try:
            r = cocha_rt(base["ida"], base["vuelta"], dst=cod)
            if r:
                brasil.append({"cod": cod, "destino": nombre, "min_clp": r[0]["clp"],
                               "aerolinea": r[0]["aerolinea"], "escalas": r[0]["escalas"],
                               "delta_pct": round((r[0]["clp"] / rio_min - 1) * 100, 1),
                               "link": f"https://www.cocha.com/resultado/vuelos/SCL/{cod}/{base['ida']}/1/Y?origin2={cod}&destination2=SCL&date2={base['vuelta']}"})
        except Exception:
            continue
    for b in brasil:
        b["delta_pct"] = round((b["min_clp"] / rio_min - 1) * 100, 1)
    brasil.sort(key=lambda b: b["min_clp"])
    return {"ventanas": ventanas, "brasil": brasil,
            "brasil_ventana": f"{base['ida']} → {base['vuelta']}"}

# ---------------- Semilla (primera corrida / respaldo) ----------------
def seed():
    live = {"updated": "seed", "next_refresh": None, "cycle": 0, "fuentes": {}}
    idas = list(csv.DictReader(open(os.path.join(SEED, "JS_idas_SCL-GIG.csv"))))
    vues = list(csv.DictReader(open(os.path.join(SEED, "JS_vueltas_GIG-SCL.csv"))))
    combos = json.load(open(os.path.join(SEED, "js_combos.json")))["combos_top"]
    live["fuentes"]["jetsmart"] = {
        "ts": "2026-10-06 14:00 (seed)", "ok": True,
        "idas": [{"fecha": r["fecha"], "vuelo": r["vuelo"], "hora": r["hora"],
                  "clp": float(r["mejor_precio_clp"]), "buckets": [r["buckets_disponibles"].split(":")[0]],
                  "bk": [[float(r["mejor_precio_clp"]), 9, r["buckets_disponibles"].split(":")[0]]]} for r in idas],
        "vueltas": [{"fecha": r["fecha"], "vuelo": r["vuelo"], "hora": r["hora"],
                     "clp": float(r["mejor_precio_clp"]), "buckets": [r["buckets_disponibles"].split(":")[0]],
                     "bk": [[float(r["mejor_precio_clp"]), 9, r["buckets_disponibles"].split(":")[0]]]} for r in vues],
        "combos": [{"ida": c[1], "vuelta": c[3], "noches": c[5], "v_ida": c[2], "v_vue": c[4], "clp": c[0], "tot": c[0]} for c in combos]}
    sky = list(csv.DictReader(open(os.path.join(SEED, "SKY_tarifas_ene-feb2027.csv"))))
    live["fuentes"]["sky"] = {"ts": "2026-10-06 14:09 (seed)", "ok": True,
                              "pairs": [{"ida": r["ida"], "vuelta": r["vuelta"], "noches": int(r["noches"]),
                                         "clp": float(r["clp"])} for r in sky]}
    lat = [r for r in csv.DictReader(open(os.path.join(SEED, "vuelos_directos_google_ene-feb.csv"))) if r["aerolinea"] == "LATAM"]
    live["fuentes"]["google"] = {"ts": "2026-10-06 17:14 (snapshot)", "ok": True,
                                 "vuelos": [{"ida": r["ida"], "vuelta": r["vuelta"], "codigo": r["codigo"],
                                              "clp": int(r["clp_pp_rt"]), "hora": r["hora_salida"],
                                              "link": glink(r["ida"], r["vuelta"])} for r in lat]}
    fal_dias = []
    try:
        for row in csv.reader(open(os.path.join(SEED, "falabella_scan_ene-feb2027.csv"))):
            if len(row) >= 3 and row[0] in ("ida SCL-RIO", "vuelta RIO-SCL"):
                fal_dias.append({"sentido": "SCL-RIO" if row[0] == "ida SCL-RIO" else "RIO-SCL",
                                 "fecha": row[1], "status": 200, "min_clp": int(row[2]), "calendario": []})
    except Exception:
        pass
    live["fuentes"]["falabella"] = {"ts": "2026-10-06 18:30 (fetcher externo)",
                                    "ts_base": "2026-10-06 18:30 (fetcher externo)", "ok": True,
                                    "nota": "caché SSR 1 adulto — solo referencia",
                                    "dias": fal_dias}
    return live

def signature(live):
    F = live.get("fuentes", {})
    js = F.get("jetsmart", {})
    sk = F.get("sky", {})
    gg = F.get("google", {})
    fa = F.get("falabella", {})
    la = F.get("latam", {})
    de = F.get("despegar", {})
    co = F.get("cocha", {})
    return json.dumps([
        [int(c["clp"]) for c in (js.get("combos") or [])[:5]],
        [int(p["clp"]) for p in (sk.get("pairs") or [])[:5]],
        [int(v["clp"]) for v in (gg.get("vuelos") or [])[:5]],
        [int(d.get("min_clp") or 0) for d in (fa.get("dias") or [])],
        [int(c["clp"]) for c in (la.get("combos") or [])[:5]],
        [int(d.get("min_clp") or 0) for d in (de.get("dias") or [])],
        int((co.get("rio") or {}).get("clp") or 0),
        [int((d.get("mejor") or {}).get("clp") or 0) for d in ((F.get("playas") or {}).get("destinos") or [])],
    ])

# ---------------- Dólar observado (usado por playas para fx informativo) ----------------
def usd_clp():
    """Dólar observado (mindicador.cl) con fallback open.er-api.com."""
    for url, path in (("https://mindicador.cl/api/dolar", ("dolar", "valor")),
                      ("https://open.er-api.com/v6/latest/USD", ("rates", "CLP"))):
        try:
            r = cr.get(url, impersonate="chrome", timeout=30)
            d = r.json()
            for k in path:
                d = d[k]
            v = float(str(d).replace(".", "").replace(",", ".")) if isinstance(d, str) else float(d)
            if 300 < v < 3000:
                return v
        except Exception:
            continue
    return 950.0

# ---------------- Playas LatAm: destinos de playa bajo presupuesto ----------------
PLAYAS_DEST = [("GIG", "Río de Janeiro", "Brasil", "🇧🇷"),
               ("FLN", "Florianópolis", "Brasil", "🇧🇷"),
               ("SSA", "Salvador de Bahía", "Brasil", "🇧🇷"),
               ("REC", "Recife · Porto de Galinhas", "Brasil", "🇧🇷"),
               ("FOR", "Fortaleza", "Brasil", "🇧🇷"),
               ("MCZ", "Maceió · Maragogi", "Brasil", "🇧🇷"),
               ("NAT", "Natal · Pipa", "Brasil", "🇧🇷"),
               ("BPS", "Porto Seguro · Arraial", "Brasil", "🇧🇷"),
               ("CTG", "Cartagena · Bocagrande", "Colombia", "🇨🇴"),
               ("ADZ", "San Andrés Isla", "Colombia", "🇨🇴"),
               ("PUJ", "Punta Cana · Bávaro", "Rep. Dominicana", "🇩🇴"),
               ("CUN", "Cancún · Riviera Maya", "México", "🇲🇽"),
               ("HAV", "La Habana · Varadero", "Cuba", "🇨🇺"),
               ("LIR", "Liberia · Guanacaste", "Costa Rica", "🇨🇷"),
               ("SJO", "San José · Jacó/Manuel Antonio", "Costa Rica", "🇨🇷"),
               ("PTY", "Panamá · Playa Blanca", "Panamá", "🇵🇦")]
PLAYAS_WIN = [("2027-01-22", "2027-02-01"), ("2027-01-10", "2027-01-20"), ("2027-01-02", "2027-02-01")]
PLAYAS_BUDGET = 500000

def agencia_rt(dest, ida, vuelta):
    """RT en vivo SCL→dest vía Cocha (agencia chilena) — reemplazo de Skiplagged
    pedido por el dueño: los datos de playas ahora vienen de una agencia real,
    no de un metebuscador. Devuelve dict o None si no hay itinerarios."""
    rows = cocha_rt(ida, vuelta, dst=dest)
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: r.get("clp") or 0)
    # preferencia por itinerarios de fecha EXACTA: si Cocha devuelve tarifas
    # flexibles de otra fecha, se descartan para el mínimo publicado
    exact = [r for r in rows
             if r.get("fecha_ida") == ida and r.get("fecha_vuelta") == vuelta]
    r0 = (exact or rows)[0]
    return {"min_clp": r0["clp"], "aerolinea": r0["aerolinea"], "cod_al": r0.get("cod", ""),
            "exacta": bool(exact) and r0 in exact,
            "fecha_ida_real": r0.get("fecha_ida", ida), "fecha_vuelta_real": r0.get("fecha_vuelta", vuelta),
            "escalas": r0.get("escalas", 0), "det": r0.get("det", ""), "top": rows[:5],
            "link": f"https://www.cocha.com/resultado/vuelos/SCL/{dest}/{ida}/1/Y?origin2={dest}&destination2=SCL&date2={vuelta}"}

def fetch_playas(prev, cycle):
    """Escaneo de destinos de playa vía Cocha (agencia chilena): 1 ventana rotativa
    por ciclo (todas al arrancar). Fusiona con la caché anterior.
    Ventanas heredadas del antiguo escaneo Skiplagged se descartan (link skiplagged)."""
    prev = prev or {}
    prev_dest = {d["cod"]: d for d in (prev.get("destinos") or []) if isinstance(d, dict)}
    full = os.environ.get("PLAYAS_FULL") == "1" or not prev_dest
    wins = list(PLAYAS_WIN) if full else [PLAYAS_WIN[cycle % len(PLAYAS_WIN)]]
    fx = usd_clp()
    out = []
    for cod, ciudad, pais, flag in PLAYAS_DEST:
        pd = prev_dest.get(cod) or {}
        # descarta ventanas legadas del metebuscador Skiplagged (fuente retirada)
        wprev = {f"{w['ida']}_{w['vuelta']}": w for w in (pd.get("windows") or [])
                 if "skiplagged.com" not in str(w.get("link") or "")}
        for ida, vuelta in wins:
            try:
                res = agencia_rt(cod, ida, vuelta)
                if res:
                    wprev[f"{ida}_{vuelta}"] = {"ida": ida, "vuelta": vuelta, "ts": now_iso(), **res}
                    flag = "exacta" if res.get("exacta") else "FLEX!"
                    print(f"playas {cod} {ida}: ${res['min_clp']:,} [{flag}]", flush=True)
                else:
                    print(f"playas {cod} {ida}: sin itinerarios", flush=True)
            except Exception as e:
                print(f"playas {cod} {ida} FAIL: {str(e)[:60]}", flush=True)
        wlist = sorted(wprev.values(), key=lambda w: w["min_clp"])
        out.append({"cod": cod, "ciudad": ciudad, "pais": pais, "flag": flag,
                    "windows": wlist,
                    "cocha": ({"min_clp": wlist[0]["min_clp"], "aerolinea": wlist[0]["aerolinea"],
                               "escalas": wlist[0]["escalas"], "det": wlist[0]["det"],
                               "ida": wlist[0]["ida"], "vuelta": wlist[0]["vuelta"],
                               "ts": wlist[0]["ts"], "link": wlist[0]["link"]} if wlist else pd.get("cocha"))})
    # mejor opción por destino (Cocha)
    for d in out:
        cands = [(w["min_clp"], "Cocha", w["ida"], w["vuelta"], w["aerolinea"], w["escalas"], w["det"], w["link"])
                 for w in d["windows"]]
        if cands:
            b = min(cands, key=lambda t: t[0])
            d["mejor"] = {"clp": b[0], "src": b[1], "ida": b[2], "vuelta": b[3], "aerolinea": b[4],
                          "escalas": b[5], "det": b[6], "link": b[7],
                          "noches": (datetime.date.fromisoformat(b[3]) - datetime.date.fromisoformat(b[2])).days}
        else:
            d["mejor"] = None
    out.sort(key=lambda d: (d["mejor"] is None, (d["mejor"] or {}).get("clp") or 0))
    if not any(d.get("mejor") for d in out):
        raise RuntimeError("playas sin resultados")
    return {"destinos": out, "fx": round(fx, 2), "budget": PLAYAS_BUDGET,
            "ventanas": [f"{a} → {b}" for a, b in PLAYAS_WIN],
            "nota": "Cotización en vivo de Cocha (agencia chilena) — precios verificados con fecha de itinerario exacta; el precio final puede variar ~10% al reservar."}

def minimos_de(live):
    """Mejor precio por serie para comparar entre ciclos (alimenta alertas)."""
    f = live.get("fuentes", {})
    def mn(rows, key="clp"):
        vals = [r.get(key) or 0 for r in (rows or []) if r.get(key)]
        return min(vals) if vals else None
    m = {}
    for k, serie, key in (("sky", "SKY SCL-Río", "pairs"), ("jetsmart", "JetSMART SCL-Río", "combos"), ("latam", "LATAM SCL-Río", "combos")):
        if (f.get(k) or {}).get("ok"):
            m[serie] = mn(f[k].get(key))
    if (f.get("cocha") or {}).get("ok"):
        m["Cocha SCL-Río"] = mn(f["cocha"].get("ventanas"), "min_clp")
    if (f.get("playas") or {}).get("ok"):
        for d in (f["playas"].get("destinos") or []):
            mj = d.get("mejor")
            if mj and mj.get("clp"):
                m[f"Playas {d['cod']}"] = mj["clp"]
    return m

def main():
    old = None
    if os.path.exists(DATA):
        try:
            old = json.load(open(DATA))
        except Exception:
            old = None
    LIVE = old if old else seed()
    cycle = int(LIVE.get("cycle", 0))

    # 1) JetSMART
    try:
        js = fetch_jetsmart()
        LIVE["fuentes"]["jetsmart"] = {"ts": now_iso(), "ok": True, **js}
        print(f"JetSMART OK: {len(js['idas'])} idas, mejor combo {js['combos'][0]['clp']:,.0f}", flush=True)
    except Exception as e:
        print(f"JetSMART FAIL: {str(e)[:100]}", flush=True)
        LIVE["fuentes"].setdefault("jetsmart", {})["ok"] = False
    # 2) SKY
    try:
        sk = fetch_sky()
        LIVE["fuentes"]["sky"] = {"ts": now_iso(), "ok": True, **sk}
        print(f"SKY OK: {len(sk['pairs'])} pares, mejor {sk['pairs'][0]['clp']:,.0f}", flush=True)
    except Exception as e:
        print(f"SKY FAIL: {str(e)[:100]}", flush=True)
        LIVE["fuentes"].setdefault("sky", {})["ok"] = False
    # 3+6) Falabella/Despegar vía navegador stealth (bypass DataDome verificado en Actions)
    # Gating anti-ban: DataDome banea IPs de Actions si se les golpea cada 5 min.
    # Se intenta como máximo cada OTAS_GAP segundos (~2.5 h), haya éxito o no, usando
    # la marca ota_ts persistida en data.json (sobrevive entre ciclos).
    OTAS_GAP = int(os.environ.get("OTAS_GAP", "9000"))
    def _parse_ts(ts):
        ts = str(ts or "")
        try:
            return datetime.datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None
    last_attempt = _parse_ts(LIVE.get("ota_ts"))
    due = (not last_attempt) or (datetime.datetime.now() - last_attempt).total_seconds() >= OTAS_GAP
    otas = {}  # resultados stealth (incluye cocha_verif); {} si este ciclo no venció el gate

    # PISO DE PLAUSIBILIDAD (fix precios erróneos): ninguna tarifa one-way real de
    # una agencia puede quedar por debajo de la aerolínea más barata para esa fecha
    # (APIs directas JetSMART/LATAM, ±1 día de holgura) menos 8%. Si queda, es un
    # precio de un módulo "fechas flexibles" de otro mes que contaminó la captura.
    def _piso_one_way(o, d, fecha):
        try:
            f0 = datetime.date.fromisoformat(fecha)
        except Exception:
            return None
        vecs = {(f0 + datetime.timedelta(days=i)).isoformat() for i in (-1, 0, 1)}
        vals = []
        jsx = LIVE["fuentes"].get("jetsmart") or {}
        for x in (jsx.get("idas") or []):
            if x.get("clp") and x.get("fecha") in vecs:
                vals.append(float(x["clp"]))
        lax = LIVE["fuentes"].get("latam") or {}
        for x in ((lax.get("dias_out") if (o, d) == ("SCL", "GIG") else lax.get("dias_in")) or []):
            if x.get("clp") and x.get("fecha") in vecs:
                vals.append(float(x["clp"]))
        return int(0.92 * min(vals)) if vals else None

    if due:
        LIVE["ota_ts"] = now_iso()  # se marca ANTES de intentar (evita martilleo si falla)
        try:
            import fetch_otas
            if not fetch_otas.HAS_CAMOUFOX:
                raise RuntimeError("sin camoufox")
            otas = fetch_otas.fetch_otas(log=lambda m: print(m, flush=True),
                                         cocha_ventanas=COCHA_VENTANAS,
                                         piso_fn=_piso_one_way)
            fa_dias = otas.get("falabella") or {}
            de_dias = otas.get("despegar") or {}
            # PRÉSTAMO CRUZADO: Despegar y Falabella comparten inventario; si a un
            # sitio le faltó un sentido verificado (grilla/API descartadas por piso)
            # y al otro no, reutilizamos su lectura — queda auditada como cruzado_*.
            for _a, _b in (("falabella", "despegar"), ("despegar", "falabella")):
                da = otas.get(_a) or {}
                db = otas.get(_b) or {}
                for dx in da.get("dias", []):
                    if dx.get("min_clp"):
                        continue
                    dy = next((y for y in db.get("dias", [])
                               if y.get("sentido") == dx.get("sentido")
                               and y.get("fecha") == dx.get("fecha") and y.get("min_clp")), None)
                    if dy:
                        dx["min_clp"] = dy["min_clp"]
                        dx["base"] = f"cruzado_{_b}"
                        dx["cruzado_de"] = _b
            if any(d.get("min_clp") for d in fa_dias.get("dias", [])):
                LIVE["fuentes"]["falabella"] = {"ts": now_iso(), "ok": True,
                    "nota": "lectura stealth 1 adulto (navegador real, DataDome bypass) — precio de referencia",
                    **fa_dias}
                print(f"Falabella CAMOUFOX OK: {fetch_otas.ok(fa_dias['dias'])} lecturas", flush=True)
            else:
                print("Falabella stealth sin precios: se conserva último dato", flush=True)
            if any(d.get("min_clp") for d in de_dias.get("dias", [])):
                LIVE["fuentes"]["despegar"] = {"ts": now_iso(), "ok": True,
                    "nota": "lectura stealth 1 adulto (navegador real, DataDome bypass) — precio de referencia",
                    **de_dias}
                print(f"Despegar CAMOUFOX OK: {fetch_otas.ok(de_dias['dias'])} lecturas", flush=True)
            else:
                print("Despegar stealth sin precios: se conserva último dato", flush=True)
        except Exception as e:
            print(f"OTAs stealth FAIL ({str(e)[:60]}) — fallback SSR", flush=True)
            if HAS_CURL:
                try:
                    fa = fetch_falabella()
                    if any(r.get("min_clp") for r in fa["dias"]):
                        LIVE["fuentes"]["falabella"] = {"ts": now_iso(), "ok": True,
                                                        "nota": "caché SSR 1 adulto — solo referencia", **fa}
                        print("Falabella SSR OK", flush=True)
                    else:
                        print("Falabella WAF: se conserva último dato", flush=True)
                except Exception as e:
                    print(f"Falabella FAIL: {str(e)[:80]}", flush=True)
                try:
                    de = fetch_despegar()
                    if any(r.get("min_clp") for r in de["dias"]):
                        LIVE["fuentes"]["despegar"] = {"ts": now_iso(), "ok": True, "nota": "SSR público — referencia", **de}
                        print("Despegar SSR OK", flush=True)
                    else:
                        print("Despegar WAF: se conserva último dato", flush=True)
                except Exception as e:
                    print(f"Despegar FAIL: {str(e)[:80]}", flush=True)
    # 5) LATAM oficial (BFF calendario)
    try:
        la = fetch_latam()
        LIVE["fuentes"]["latam"] = {"ts": now_iso(), "ok": True, **la}
        print(f"LATAM OK: {len(la['dias_out'])}+{len(la['dias_in'])} días, mejor combo {la['combos'][0]['clp']:,.0f}", flush=True)
    except Exception as e:
        print(f"LATAM FAIL: {str(e)[:100]}", flush=True)
        cur = LIVE["fuentes"].get("latam")
        if cur is not None:
            cur["ok"] = False
    # 7) Cocha: cotización REAL por API + verificación del mínimo contra la página
    #    real de resultados (la API sub-cotiza ~10-13%; solo publicamos lo que la
    #    página cobra — misma regla de honestidad que Despegar/Falabella).
    try:
        co_live = fetch_cocha_live(LIVE["fuentes"].get("cocha") or {})
        verif = (otas if isinstance(otas, dict) else {}).get("cocha_verif") or {}
        n_verif = 0
        for v in co_live.get("ventanas", []):
            vr = verif.get(v.get("ida")) or {}
            api_min = v.get("min_clp")
            pag = vr.get("pag_min")
            if pag and api_min and pag >= api_min * 0.85:
                # verificado en la página: manda el precio real (api_min queda como auditoría)
                v["api_min"] = api_min
                v["min_clp"] = pag
                v["base"] = "pagina"
                v["n_vuelos"] = vr.get("n_vuelos")
                n_verif += 1
            else:
                v["base"] = "api"
                if pag:
                    v["pagina_no_coherente"] = pag
        old = LIVE["fuentes"].get("cocha") or {}
        LIVE["fuentes"]["cocha"] = {"ts": now_iso(), "ok": True,
            "nota": ("Cotización automática de cocha.com: detalle por vuelo vía API oficial; "
                     "el mínimo de cada ventana se verifica contra la página real de "
                     "resultados (1 adulto, ida y vuelta). Si la verificación no está "
                     "disponible, se declara la cotización API y puede variar ~10% al reservar."),
            "rio": old.get("rio"), "cards": old.get("cards", []),
            "verificadas": n_verif,
            "link": "https://www.cocha.com/vuelos", **co_live}
        print("Cocha LIVE OK: " + " | ".join(
            f"{v['ida']}→{v['vuelta']} ${v['min_clp']:,.0f}[{v.get('base')}]{(' vs api $' + format(v['api_min'], ',')) if v.get('api_min') else ''}"
            for v in co_live["ventanas"]), flush=True)
    except Exception as e:
        print(f"Cocha live FAIL: {str(e)[:90]}", flush=True)
        try:
            co = fetch_cocha()
            if co.get("rio"):
                co_old = LIVE["fuentes"].get("cocha") or {}
                ventanas = co_old.get("ventanas", [])
                # incluso sin API, la verificación de página (navegador+WARP) puede
                # actualizar los mínimos: el tile no depende de la API de Cocha
                verif = (otas if isinstance(otas, dict) else {}).get("cocha_verif") or {}
                n_verif = 0
                for v in ventanas:
                    vr = verif.get(v.get("ida")) or {}
                    pag = vr.get("pag_min")
                    if pag:
                        v["api_min"] = v.get("api_min") or v.get("min_clp")
                        v["min_clp"] = pag
                        v["base"] = "pagina"
                        v["n_vuelos"] = vr.get("n_vuelos")
                        n_verif += 1
                LIVE["fuentes"]["cocha"] = {"ts": now_iso(), "ok": True,
                    "nota": ("Cotización automática de cocha.com: mínimo de cada ventana "
                             "verificado contra la página real de resultados (1 adulto, "
                             "ida y vuelta). API de detalle no disponible este ciclo; "
                             "resultados por vuelo = última cotización."),
                    "ventanas": ventanas, "brasil": co_old.get("brasil", []),
                    "brasil_ventana": co_old.get("brasil_ventana"), "verificadas": n_verif, **co}
                print(f"Cocha SSR+verif OK: {n_verif} ventanas verificadas en página", flush=True)
        except Exception as e2:
            print(f"Cocha SSR FAIL: {str(e2)[:80]}", flush=True)
    # 8) Playas LatAm: destinos de playa bajo presupuesto $500K (Cocha, agencia chilena)
    try:
        pl = fetch_playas(LIVE["fuentes"].get("playas") or {}, cycle)
        LIVE["fuentes"]["playas"] = {"ts": now_iso(), "ok": True, **pl}
        bajos = [d for d in pl["destinos"] if d.get("mejor") and d["mejor"]["clp"] < pl["budget"]]
        print(f"Playas OK: {len(bajos)}/{len(pl['destinos'])} bajo ${pl['budget']:,}" +
              (f" | mejor: {bajos[0]['flag']} {bajos[0]['ciudad']} ${bajos[0]['mejor']['clp']:,}" if bajos else ""), flush=True)
    except Exception as e:
        print(f"Playas FAIL: {str(e)[:90]}", flush=True)
        cur = LIVE["fuentes"].get("playas")
        if cur is not None:
            cur["ok"] = False
    # (espejo Google eliminado por pedido del usuario)
    LIVE["fuentes"].pop("google", None)

    LIVE["cycle"] = cycle + 1
    LIVE["checked"] = now_iso()
    LIVE["updated"] = now_iso()
    LIVE["next_refresh"] = None
    sig = signature(LIVE)
    if old and old.get("sig") == sig:
        print("SIN CAMBIOS de precios (verificación registrada)", flush=True)
    else:
        LIVE["last_price_change"] = now_iso()
        print("CAMBIO DE PRECIOS detectado", flush=True)
    LIVE["sig"] = sig
    # ---- Alertas de bajada de precio (motor de notificación por correo) ----
    try:
        prev = {}
        if os.path.exists(DATA):
            with open(DATA, encoding="utf8") as fh:
                prev = json.load(fh)
        minimos = minimos_de(LIVE)
        alerts = list(prev.get("alerts") or [])
        nuevos = []
        for serie, ahora in minimos.items():
            if not ahora:
                continue
            antes = (prev.get("minimos") or {}).get(serie)
            if antes and ahora < antes and (antes - ahora) >= 1000 and (antes - ahora) / antes >= 0.005:
                pct = round((antes - ahora) / antes * 100, 1)
                nuevos.append({"ts": now_iso(), "serie": serie, "antes": antes,
                               "ahora": ahora, "baja": antes - ahora, "pct": pct})
        if nuevos:
            alerts = (alerts + nuevos)[-30:]
            LIVE["email_pending"] = True
            print("ALERTA: " + "; ".join(f"{n['serie']} ${n['antes']:,}→${n['ahora']:,}" for n in nuevos), flush=True)
        else:
            LIVE["email_pending"] = bool(prev.get("email_pending"))
        # limpieza única: las series del metebuscador retirado desaparecen del panel
        # y del historial de alertas (el usuario pidió retirar esa fuente por completo)
        alerts = [a for a in alerts if "skiplagged" not in str(a.get("serie", "")).lower()]
        LIVE["alerts"] = alerts
        LIVE["minimos"] = minimos
    except Exception as e:
        print(f"alerts skip: {str(e)[:80]}", flush=True)
    tmp = DATA + ".tmp"
    # Preservar secciones gestionadas por fetchers aparte (fetch_coupons.py)
    try:
        if os.path.exists(DATA):
            with open(DATA, encoding="utf8") as fh:
                _prev_all = json.load(fh)
            if _prev_all.get("cupones") and not LIVE.get("cupones"):
                LIVE["cupones"] = _prev_all["cupones"]
    except Exception:
        pass
    with open(tmp, "w") as fh:
        json.dump(LIVE, fh, ensure_ascii=False)
    os.replace(tmp, DATA)
    print("data.json actualizado", flush=True)

if __name__ == "__main__":
    main()
