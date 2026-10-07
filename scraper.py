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

# ---------------- Falabella (referencia cacheada) ----------------
FAL_DATES = [("SCL", "RIO", d) for d in ("2027-01-06", "2027-01-20", "2027-01-22")] + \
            [("RIO", "SCL", d) for d in ("2027-02-01", "2027-02-08")]

def fetch_falabella():
    rows = []
    for o, d, fecha in FAL_DATES:
        try:
            u = f"https://www.viajesfalabella.cl/shop/flights/results/oneway/{o}/{d}/{fecha}/1/0/0"
            r = cr.get(u, impersonate="chrome", timeout=45,
                       headers={"Accept-Language": "es-CL,es;q=0.9"})
            h = r.text
            precios = [int(p.replace(".", "")) for p in re.findall(r"clickedPrice=CLP_(\d+)", h)]
            cards = [int(p.replace(".", "")) for p in re.findall(r">\s*\$\s?(\d{2,3}\.\d{3})\s*<", h)]
            mejor = min(precios + cards) if (precios or cards) else None
            cal = []
            for m in re.finditer(r"oneway/[a-z]{3}/[a-z]{3}/(\d{4}-\d{2}-\d{2})/\d/\d/\d\?refererEvent=calendarPricesMatrix&clickedPrice=CLP_(\d+)", h):
                cal.append({"fecha": m.group(1), "clp": int(m.group(2))})
            rows.append({"sentido": f"{o}-{d}", "fecha": fecha, "status": r.status_code,
                         "min_clp": mejor, "calendario": cal})
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
        if r.status_code != 200:
            rows.append({"sentido": f"{o}-{d}", "fecha": fecha, "status": r.status_code, "min_clp": None, "calendario": []})
            continue
        h = r.text
        precios = [int(p.replace(".", "")) for p in re.findall(r"clickedPrice=CLP_(\d+)", h)]
        cards = [int(p.replace(".", "")) for p in re.findall(r">\s*\$\s?(\d{2,3}\.\d{3})\s*<", h)]
        cal = [{"fecha": m.group(1), "clp": int(m.group(2))} for m in re.finditer(
            r"oneway/[a-z]{3}/[a-z]{3}/(\d{4}-\d{2}-\d{2})/\d/\d/\d\?refererEvent=calendarPricesMatrix&clickedPrice=CLP_(\d+)", h)]
        rows.append({"sentido": f"{o}-{d}", "fecha": fecha, "status": 200,
                     "min_clp": min(precios + cards) if (precios or cards) else None, "calendario": cal})
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
    ])

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
    # 3) Falabella (conserva último dato si WAF bloquea)
    if HAS_CURL:
        try:
            fa = fetch_falabella()
            if any(r.get("min_clp") for r in fa["dias"]):
                LIVE["fuentes"]["falabella"] = {"ts": now_iso(), "ok": True,
                                                "nota": "caché SSR 1 adulto — solo referencia", **fa}
                print("Falabella OK", flush=True)
            else:
                print("Falabella WAF: se conserva último dato", flush=True)
        except Exception as e:
            print(f"Falabella FAIL: {str(e)[:80]}", flush=True)
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
    # 6) Despegar SSR
    try:
        de = fetch_despegar()
        if any(r.get("min_clp") for r in de["dias"]):
            LIVE["fuentes"]["despegar"] = {"ts": now_iso(), "ok": True, "nota": "SSR público — referencia", **de}
            print("Despegar OK", flush=True)
        else:
            print("Despegar WAF: se conserva último dato", flush=True)
    except Exception as e:
        print(f"Despegar FAIL: {str(e)[:80]}", flush=True)
    # 7) Cocha SSR
    try:
        co = fetch_cocha()
        if co.get("rio"):
            LIVE["fuentes"]["cocha"] = {"ts": now_iso(), "ok": True, **co}
            print(f"Cocha OK: Río {co['rio']['clp']:,}", flush=True)
        else:
            print("Cocha sin tarjeta Río", flush=True)
    except Exception as e:
        print(f"Cocha FAIL: {str(e)[:80]}", flush=True)
    # 4) Google espejo (1 ventana rotativa)
    try:
        gg = fetch_google(cycle)
        oldv = LIVE["fuentes"].get("google", {}).get("vuelos", [])
        new = [dict(v, ida=gg["ventana"][0], vuelta=gg["ventana"][1], link=gg["link"]) for v in gg["vuelos"]]
        merged = [v for v in oldv if (v.get("ida"), v.get("vuelta")) != tuple(gg["ventana"])] + new
        merged.sort(key=lambda v: v["clp"])
        LIVE["fuentes"]["google"] = {"ts": now_iso(), "ok": True, "vuelos": merged}
        print(f"Google OK: ventana {gg['ventana']}", flush=True)
    except Exception as e:
        print(f"Google skip: {str(e)[:80]}", flush=True)

    LIVE["cycle"] = cycle + 1
    LIVE["updated"] = now_iso()
    LIVE["next_refresh"] = None
    sig = signature(LIVE)
    if old and old.get("sig") == sig:
        print("SIN CAMBIOS de precios — no se reescribe data.json", flush=True)
        return
    LIVE["sig"] = sig
    tmp = DATA + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(LIVE, fh, ensure_ascii=False)
    os.replace(tmp, DATA)
    print("data.json actualizado", flush=True)

if __name__ == "__main__":
    main()
