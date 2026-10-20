#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auditoría de módulos 4×/día — decide si los precios publicados son REALES
y si TODOS los módulos del sitio son funcionales.

Qué hace:
 1. Inventario y sanidad de cada módulo del data.json (frescura, plausibilidad,
    conteos, flags ok, assets, consistencia cruzada).
 2. MUESTRA VIVA rotativa: re-captura en vivo una muestra de cada módulo clave
    (3 destinos de playa + 1 fuente de vuelo por auditoría) y la compara contra
    lo publicado. Si difiere > tolerancia en playas → lo CORRIGE en data.json.
 3. Detección de módulos CONGELADOS (mismos precios 3 auditorías seguidas) y
    re-captura completa de playas cuando eso pasa.
 4. Funcional del sitio: corre verify.js (+ scripts/test_tiles.js si existe).
 5. Publica auditoria.json (insignia visible en el sitio) y auditoria_previa.json.

Nunca bloquea la publicación (exit 0 salvo data.json ilegible).
"""
import hashlib
import json
import os
import subprocess
import sys
import traceback
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
DATA = "data.json"
PREVIO = "auditoria_previa.json"

NOW = datetime.datetime.utcnow()
TS = NOW.strftime("%Y-%m-%d %H:%M:%S")
TS_STGO = (NOW + datetime.timedelta(hours=-3)).strftime("%Y-%m-%d %H:%M:%S")

COL = {"ok": "#10b981", "alerta": "#d97706", "fallo": "#dc2626", "neutro": "#64748b"}

MODULOS = []


def add(mid, nombre, estado, detalle, frescura_h=None, muestra=None):
    est = estado.upper()
    color = COL["ok"] if est in ("OK", "VERIFICADO", "CORREGIDO") else (
        COL["alerta"] if est in ("ALERTA", "CONGELADO", "NO VERIFICABLE") else COL["fallo"])
    MODULOS.append({
        "id": mid, "nombre": nombre, "estado": estado, "color": color,
        "frescura_h": (round(frescura_h, 1) if isinstance(frescura_h, (int, float)) else None),
        "detalle": detalle[:300],
        "muestra": muestra or []
    })
    print(f"  [{estado}] {nombre}: {detalle[:150]}", flush=True)


def parse_ts(s):
    try:
        return datetime.datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def horas(ts):
    t = parse_ts(ts)
    if not t:
        return None
    return max(0.0, (NOW - t).total_seconds() / 3600.0)


def htxt(h):
    if h is None:
        return "sin ts"
    return f"{h:.1f} h" if h < 48 else f"{h / 24:.1f} días"


def sha(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def main():
    ok_n = alerta_n = fallo_n = 0
    cambios_data = [False]  # flag: se corrigió algo en data.json

    def cuenta(estado):
        nonlocal ok_n, alerta_n, fallo_n
        e = estado.lower()
        if e in ("ok", "verificado", "corregido"):
            ok_n += 1
        elif e in ("alerta", "congelado", "no verificable"):
            alerta_n += 1
        else:
            fallo_n += 1

    if not os.path.exists(DATA):
        print("data.json no existe — nada que auditar"); sys.exit(2)
    try:
        data = json.load(open(DATA, encoding="utf-8"))
    except Exception as e:
        print(f"data.json ilegible: {e}"); sys.exit(2)

    prev = {}
    if os.path.exists(PREVIO):
        try:
            prev = json.load(open(PREVIO, encoding="utf-8"))
        except Exception:
            prev = {}
    n_audit = int(prev.get("n") or 0) + 1
    hashes_prev = prev.get("hashes") or {}
    hashes_now = {}
    congelado_streak = int(prev.get("congelado_streak") or 0)

    fuentes = data.get("fuentes") or {}

    # ---------- 1. fuentes de vuelos (frescura + plausibilidad) ----------
    VUELOS = [("jetsmart", "JetSMART", 26), ("sky", "SKY", 26), ("latam", "LATAM", 26),
              ("cocha", "Cocha", 30), ("despegar", "Despegar", 30), ("falabella", "Viajes Falabella", 30)]
    minimos_vuelos = {}
    for key, nombre, maxh in VUELOS:
        f = fuentes.get(key)
        if not isinstance(f, dict):
            add(f"vuelos-{key}", f"Vuelos {nombre}", "ALERTA", "módulo ausente en data.json")
            cuenta("alerta")
            continue
        e = horas(f.get("ts"))
        if not f.get("ok"):
            add(f"vuelos-{key}", f"Vuelos {nombre}", "ALERTA", f"flag ok=false (último ciclo sin captura) · última data hace {htxt(e)}")
            cuenta("alerta")
            continue
        if e is None:
            add(f"vuelos-{key}", f"Vuelos {nombre}", "FALLA", "sin timestamp")
            cuenta("fallo")
            continue
        # plausibilidad del mínimo
        minimo, n_reg = None, 0
        for campo in ("combos", "pairs", "ventanas", "resultados", "ida", "vuelta", "dias"):
            rows = f.get(campo)
            if isinstance(rows, list) and rows:
                precios = [r.get("clp") or r.get("min_clp") or 0 for r in rows if isinstance(r, dict)]
                precios = [p for p in precios if p]
                if precios:
                    minimo = min(precios) if minimo is None else min(minimo, min(precios))
                    n_reg += len(precios)
        if minimo is None:
            add(f"vuelos-{key}", f"Vuelos {nombre}", "ALERTA", f"ok pero sin precios legibles ({htxt(e)})")
            cuenta("alerta")
            continue
        if not (60000 <= minimo <= 3000000):
            add(f"vuelos-{key}", f"Vuelos {nombre}", "FALLA", f"precio fuera de rango plausible: ${minimo:,}")
            cuenta("fallo")
            continue
        estado = "OK" if e <= maxh else "ALERTA"
        det = f"mínimo ${minimo:,} · {n_reg} registros · fresco {htxt(e)} (límite {maxh} h)"
        if estado != "OK":
            det += " — la cadena de refresh debería refrescarlo en el próximo ciclo"
        hashes_now[f"vuelos-{key}"] = sha([minimo, n_reg])
        add(f"vuelos-{key}", f"Vuelos {nombre}", estado, det, frescura_h=e)
        cuenta(estado)
        minimos_vuelos[key] = minimo

    # consistencia cruzada Despegar vs Falabella (mismo mercado, no deben divergir >45%)
    if minimos_vuelos.get("despegar") and minimos_vuelos.get("falabella"):
        a, b = minimos_vuelos["despegar"], minimos_vuelos["falabella"]
        div = abs(a - b) / max(1, min(a, b))
        if div > 0.45:
            add("consistencia-otas", "Consistencia Despegar↔Falabella", "ALERTA",
                f"difieren {div * 100:.0f}% (${a:,} vs ${b:,}) — revisar captura de alguna de las dos")
            cuenta("alerta")
        else:
            add("consistencia-otas", "Consistencia Despegar↔Falabella", "OK",
                f"diferencia {div * 100:.0f}% (${a:,} vs ${b:,}) — coherentes")
            cuenta("ok")

    # ---------- 2. PLAYAS: sanidad + MUESTRA VIVA rotativa + auto-corrección ----------
    playas = fuentes.get("playas") or {}
    dests = [d for d in (playas.get("destinos") or []) if isinstance(d, dict)]
    if not dests:
        add("playas", "Playas del extranjero", "FALLA", "sin destinos en data.json")
        cuenta("fallo")
    else:
        e = horas(playas.get("ts"))
        exactas = flex = 0
        precios_ok = True
        for d in dests:
            for w in (d.get("windows") or []):
                if w.get("exacta") is True:
                    exactas += 1
                elif w.get("exacta") is False:
                    flex += 1
                p = w.get("min_clp") or 0
                if not (200000 <= p <= 3500000):
                    precios_ok = False
        mejor = [(d["cod"], (d.get("mejor") or {}).get("clp")) for d in dests if d.get("mejor")]
        mn = min([p for _, p in mejor]) if mejor else None
        estado = "OK"
        det = (f"{len(dests)} destinos · {len(mejor)} con precio · mínimo ${mn:,} · "
               f"ventanas exactas {exactas}/flex {flex} · fresco {htxt(e)}")
        if not precios_ok:
            estado = "FALLA"
            det += " · PRECIO FUERA DE RANGO"
        elif e is not None and e > 30:
            estado = "ALERTA"
            det += " — datos viejos, forzando recaptura completa"
        hashes_now["playas"] = sha(sorted(mejor))

        # --- muestra viva: 6 destinos rotativos (toda la lista cada ~3 auditorías) ---
        muestra = []
        corrigio = []
        try:
            sys.path.insert(0, BASE)
            import scraper as S
            orden = sorted(dests, key=lambda d: ((d.get("mejor") or {}).get("clp") or 9e9))
            lote = [orden[(n_audit * 6 + i) % len(orden)] for i in range(6)]
            for d in lote:
                cod = d.get("cod")
                mejor_w = (d.get("windows") or [None])[0]
                if not cod or not mejor_w:
                    continue
                ida, vuelta, pub = mejor_w.get("ida"), mejor_w.get("vuelta"), mejor_w.get("min_clp")
                try:
                    res = S.agencia_rt(cod, ida, vuelta)
                    if not res:
                        muestra.append({"cod": cod, "pub": pub, "veredicto": "NO VERIFICABLE", "nota": "sin itinerarios en vivo"})
                        continue
                    vivo = res.get("min_clp")
                    diff = abs(vivo - pub) / max(1, pub)
                    v = {"cod": cod, "ida": ida, "vuelta": vuelta, "pub": pub, "vivo": vivo,
                         "diff_pct": round(diff * 100, 1),
                         "exacta": bool(res.get("exacta")),
                         "aerolinea": res.get("aerolinea")}
                    if diff <= 0.07:
                        v["veredicto"] = "VERIFICADO"
                    else:
                        v["veredicto"] = "CORREGIDO"
                        # auto-corrección en data.json
                        for w in d.get("windows") or []:
                            if w.get("ida") == ida and w.get("vuelta") == vuelta:
                                w.update({"min_clp": vivo, "ts": TS, "exacta": bool(res.get("exacta")),
                                          "aerolinea": res.get("aerolinea"), "det": res.get("det", ""),
                                          "verificado": TS})
                        d["windows"] = sorted(d.get("windows") or [], key=lambda w: w.get("min_clp") or 9e9)
                        w0 = (d.get("windows") or [{}])[0]
                        d["mejor"] = {"clp": w0.get("min_clp"), "src": "Cocha", "ida": w0.get("ida"),
                                      "vuelta": w0.get("vuelta"), "aerolinea": w0.get("aerolinea"),
                                      "escalas": w0.get("escalas", 0), "det": w0.get("det", ""),
                                      "link": w0.get("link", ""),
                                      "noches": (datetime.date.fromisoformat(str(w0.get("vuelta"))) -
                                                 datetime.date.fromisoformat(str(w0.get("ida")))).days
                                      if w0.get("ida") and w0.get("vuelta") else None}
                        d["cocha"] = {"min_clp": w0.get("min_clp"), "aerolinea": w0.get("aerolinea"),
                                      "escalas": w0.get("escalas", 0), "det": w0.get("det", ""),
                                      "ida": w0.get("ida"), "vuelta": w0.get("vuelta"),
                                      "ts": w0.get("ts"), "link": w0.get("link", "")}
                        try:
                            data["minimos"][f"Playas {cod}"] = w0.get("min_clp")
                        except Exception:
                            pass
                        corrigio.append(cod)
                        cambios_data[0] = True
                    muestra.append(v)
                except Exception as ex:
                    muestra.append({"cod": cod, "pub": pub, "veredicto": "NO VERIFICABLE",
                                    "nota": str(ex)[:80]})
        except Exception as ex:
            print(f"muestra viva playas falló: {ex}", flush=True)
        verif = [m for m in muestra if m.get("veredicto") == "VERIFICADO"]
        correg = [m for m in muestra if m.get("veredicto") == "CORREGIDO"]
        nover = [m for m in muestra if m.get("veredicto") == "NO VERIFICABLE"]
        if correg:
            det += f" · CORREGIDOS EN VIVO: {','.join(c['cod'] for c in correg)}"
            estado = "CORREGIDO"
            playas["ts"] = TS
        elif verif:
            det += f" · muestra viva verificada {len(verif)}/{len(muestra)}"
        if nover and not verif:
            estado = "NO VERIFICABLE" if estado == "OK" else estado
        det += f" (exactas {exactas}, flex {flex})"
        add("playas", "Playas del extranjero", estado, det, frescura_h=e, muestra=muestra)
        cuenta(estado)

        # --- congelado: mismos precios 3 auditorías seguidas → recaptura completa ---
        if prev.get("hashes", {}).get("playas") == hashes_now["playas"]:
            streak = congelado_streak + 1
        else:
            streak = 0
        if streak >= 3 and os.environ.get("PLAYAS_FULL") != "1":
            print("playas congelado 3 auditorías seguidas — recaptura COMPLETA en vivo", flush=True)
            try:
                sys.path.insert(0, BASE)
                import scraper as S
                os.environ["PLAYAS_FULL"] = "1"
                ciclo = int(data.get("cycle") or 0)
                nueva = S.fetch_playas(playas, ciclo)
                if nueva and nueva.get("destinos"):
                    fuentes["playas"] = {"ts": TS, "ok": True, **nueva}
                    cambios_data[0] = True
                    for d in nueva["destinos"]:
                        m = (d.get("mejor") or {}).get("clp")
                        if m:
                            data["minimos"][f"Playas {d['cod']}"] = m
                    det = f"CONGELADO {streak} auditorías → recaptura completa ejecutada ({len(nueva['destinos'])} destinos)"
                    add("playas-recaptura", "Playas — recaptura anti-congelamiento", "CORREGIDO", det)
                    cuenta("corregido")
                    streak = 0
            except Exception as ex:
                add("playas-recaptura", "Playas — recaptura anti-congelamiento", "ALERTA",
                    f"recaptura falló: {str(ex)[:120]}")
                cuenta("alerta")
        prev["congelado_streak"] = streak

    # ---------- 2b. MUESTRA VIVA de vuelos (2 fuentes rápidas por auditoría) ----------
    RAPIDAS = [("jetsmart", "JetSMART", "fetch_jetsmart"), ("sky", "SKY", "fetch_sky"), ("latam", "LATAM", "fetch_latam")]
    for i in range(2):
        key, nombre, fn = RAPIDAS[(n_audit * 2 + i) % 3]
        f = fuentes.get(key) or {}
        pub = None
        for campo in ("combos", "pairs"):
            rows = f.get(campo)
            if isinstance(rows, list) and rows:
                vals = [r.get("clp") or 0 for r in rows if isinstance(r, dict)]
                vals = [v for v in vals if v]
                if vals:
                    pub = min(vals) if pub is None else min(pub, min(vals))
        if not pub:
            continue
        try:
            import scraper as S
            nueva = getattr(S, fn)()
            vivo = None
            for campo in ("combos", "pairs"):
                rows = nueva.get(campo)
                if isinstance(rows, list) and rows:
                    vals = [r.get("clp") or 0 for r in rows if isinstance(r, dict)]
                    vals = [v for v in vals if v]
                    if vals:
                        vivo = min(vals) if vivo is None else min(vivo, min(vals))
            if not vivo:
                add(f"viva-{key}", f"Muestra viva {nombre}", "NO VERIFICABLE", "la fuente no devolvió precios en vivo")
                cuenta("alerta")
                continue
            diff = abs(vivo - pub) / max(1, pub)
            if diff <= 0.12:
                add(f"viva-{key}", f"Muestra viva {nombre}", "VERIFICADO",
                    f"publicado ${pub:,} ≈ vivo ${vivo:,} ({diff * 100:.1f}%) — el precio publicado es REAL")
                cuenta("ok")
            else:
                add(f"viva-{key}", f"Muestra viva {nombre}", "ALERTA",
                    f"publicado ${pub:,} vs vivo ${vivo:,} ({diff * 100:.0f}%) — desactualizado; el próximo refresh de la cadena lo corrige")
                cuenta("alerta")
        except Exception as ex:
            add(f"viva-{key}", f"Muestra viva {nombre}", "NO VERIFICABLE", f"captura falló: {str(ex)[:90]}")
            cuenta("alerta")

    # ---------- 3. cupones ----------
    cup = data.get("cupones") or {}
    if isinstance(cup, dict) and cup.get("ts"):
        e = horas(cup.get("ts"))
        items = cup.get("items")
        n_it = len(items) if isinstance(items, list) else (items or 0)
        if e <= 168 and n_it >= 5:
            add("cupones", "Cupones de descuento", "OK", f"{n_it} cupones vigentes · fresco {htxt(e)}", frescura_h=e)
            cuenta("ok")
            hashes_now["cupones"] = sha([n_it, str(sorted([str(x)[:40] for x in items]))[:200] if isinstance(items, list) else ""])
        else:
            add("cupones", "Cupones de descuento", "ALERTA", f"{n_it} cupones · fresco {htxt(e)} (límite 7 días)")
            cuenta("alerta")

    # ---------- 4. radar de alojamientos (stays) ----------
    stays = data.get("stays")
    if isinstance(stays, dict) and stays.get("ts"):
        e = horas(stays.get("ts"))
        dd = stays.get("destinos") or []
        con_precio = [x for x in dd if isinstance(x, dict) and (x.get("clp") or x.get("min_clp"))]
        if e <= 30 and con_precio:
            add("stays", "Radar de alojamientos", "OK", f"{len(con_precio)}/{len(dd)} destinos con precio · fresco {htxt(e)}", frescura_h=e)
            cuenta("ok")
            hashes_now["stays"] = sha([(x.get("dest"), x.get("clp") or x.get("min_clp")) for x in con_precio])
        else:
            add("stays", "Radar de alojamientos", "ALERTA", f"{len(con_precio)}/{len(dd)} destinos con precio · fresco {htxt(e)}")
            cuenta("alerta")

    # ---------- 5. resumen nocturno (noche) ----------
    noche = data.get("noche")
    if isinstance(noche, dict) and noche.get("fecha"):
        add("noche", "Resumen nocturno (digest matutino)", "OK", f"fecha {noche.get('fecha')} · series {len(noche.get('minimos') or {})}")
        cuenta("ok")

    # ---------- 6. minimos / alertas / suscriptores ----------
    mini = data.get("minimos") or {}
    if len(mini) >= 10:
        add("minimos", "Mínimos por serie (alertas)", "OK", f"{len(mini)} series activas")
        cuenta("ok")
    else:
        add("minimos", "Mínimos por serie (alertas)", "ALERTA", f"solo {len(mini)} series")
        cuenta("alerta")
    sus = data.get("suscriptores") or {}
    if isinstance(sus, dict):
        add("suscriptores", "Suscriptores de alertas", "OK", f"{sus.get('total', 0)} suscriptos (privacidad: correos cifrados)")
        cuenta("ok")

    # ---------- 7. assets de destinos ----------
    faltan = []
    for d in dests:
        cod = d.get("cod")
        if cod and not os.path.exists(os.path.join("assets", "dest", f"{cod}.jpg")):
            faltan.append(cod)
    if faltan:
        add("assets", "Imágenes de destinos", "ALERTA", f"faltan {len(faltan)}: {','.join(faltan)}")
        cuenta("alerta")
    else:
        add("assets", "Imágenes de destinos", "OK", f"{len(dests)} imágenes presentes")
        cuenta("ok")

    # ---------- 8. funcional del sitio ----------
    sitio = {}
    try:
        r = subprocess.run(["node", "verify.js", "index.html", "data.json"],
                           capture_output=True, text=True, timeout=180)
        tail = (r.stdout or "").strip().splitlines()
        resumen = tail[-1] if tail else ("sin salida" if r.returncode == 0 else "fallo")
        sitio["verify_js"] = ("OK · " + resumen) if r.returncode == 0 else f"FALLA: {resumen[:120]}"
        sitio["verify_code"] = r.returncode
    except Exception as ex:
        sitio["verify_js"] = f"ERROR {str(ex)[:80]}"
        sitio["verify_code"] = 1
    if os.path.exists(os.path.join("scripts", "test_tiles.js")):
        try:
            r = subprocess.run(["node", "scripts/test_tiles.js"], capture_output=True, text=True, timeout=180)
            tail = (r.stdout or "").strip().splitlines()
            resumen = tail[-1] if tail else "sin salida"
            sitio["test_tiles"] = ("OK · " + resumen) if r.returncode == 0 else f"FALLA: {resumen[:120]}"
        except Exception as ex:
            sitio["test_tiles"] = f"ERROR {str(ex)[:80]}"
    estado_sitio = "OK" if sitio.get("verify_code") == 0 else "FALLA"
    add("sitio", "Funcional del sitio (arneses de regresión)", estado_sitio,
        f"verify.js {sitio.get('verify_js', '')}" + (f" · tiles {sitio.get('test_tiles', '')}" if sitio.get("test_tiles") else ""))
    cuenta(estado_sitio)
    sitio.pop("verify_code", None)

    # ---------- reporte ----------
    rep = {
        "ts": TS, "ts_stgo": TS_STGO, "n": n_audit,
        "total": len(MODULOS), "ok": ok_n, "alertas": alerta_n, "fallos": fallo_n,
        "modulos": MODULOS, "sitio": sitio,
        "resumen": f"{ok_n}/{len(MODULOS)} módulos OK" + (f" · {alerta_n} alertas" if alerta_n else "") + (f" · {fallo_n} fallos" if fallo_n else ""),
    }
    with open("auditoria.json", "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)

    previo = {"n": n_audit, "ts": TS, "hashes": hashes_now, "congelado_streak": prev.get("congelado_streak", 0)}
    with open(PREVIO, "w", encoding="utf-8") as fh:
        json.dump(previo, fh, ensure_ascii=False, indent=1)

    # data.json solo se reescribe si hubo correcciones (mantiene el diff limpio)
    if cambios_data[0]:
        with open(DATA, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        print("data.json actualizado con correcciones en vivo", flush=True)

    print(f"\nAUDITORÍA {n_audit}: {rep['resumen']}", flush=True)
    print(f"sitio: {sitio}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        # el reporte parcial mejor que nada
        try:
            json.dump({"ts": TS, "ts_stgo": TS_STGO, "total": len(MODULOS), "ok": 0,
                       "alertas": 0, "fallos": 1, "modulos": MODULOS, "sitio": {},
                       "resumen": "auditoría interrumpida por error"}, open("auditoria.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            pass
        sys.exit(1)
