#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reparar.py — Auto-reparación NUMERADA de todos los módulos de la web (M01..Mnn).

TASK 25: se ejecuta desde el workflow 'auto-reparacion', que se dispara SOLO
cuando 'refresh' o 'auditoria-modulos' terminan en failure (o por dispatch
manual). El registro canónico de módulos es modulos.json — este script barre
TODOS los módulos en orden y repara lo reparable sin intervención humana.

Fases:
  0. GATE sobre el dato PUBLICADO (node verify.js). Si el publicado está sano,
     la falla del ciclo fue ruido del dato fresco — se reporta y continúa.
  1. BARRIDO de diagnóstico M01..Mnn sobre data.json (presencia, frescura,
     plausibilidad) con anotaciones ::notice::/::error:: numeradas.
  2. RE-CAPTURA COMPLETA (la mayoría de las fallas son ruido transitorio de
     red): scraper.py + fetch_coupons.py forzado. Se omite si el dato publicado
     pasa el gate Y la última auto-reparación es menor a COOLDOWN_MIN
     (anti-bucle: la reparación que no puede arreglar nada no debe ciclar).
  3. RE-BARRIDO M01..Mnn + GATE verify.js final.
  4. Escribe reparacion.json (reporte) + estado_reparacion.json (anti-bucle).

Exit 0  → el workflow publica data.json + reparacion.json.
Exit 1  → quedó algo irreparable; las anotaciones [M##] del run nombran el
          módulo exacto y el motivo (visible sin login en la página del run).
"""
import glob
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

COOLDOWN_MIN = 90          # anti-bucle: gap mínimo entre recapturas completas
SCRAPER_TIMEOUT = 1250     # s (el workflow da 32 min totales)
COUPONS_TIMEOUT = 300

# límites de frescura (h) por módulo, alineados con auditoria.py
LIMIT_H = {
    "vuelos-jetsmart": 30, "vuelos-sky": 30, "vuelos-latam": 30,
    "vuelos-cocha": 30, "vuelos-despegar": 36, "vuelos-falabella": 36,
    "playas": 30, "cupones": 168, "combos": 30, "stays": 48, "noche": 30,
}


def ann(kind, msg):
    print(f"::{kind}::{msg[:250]}", flush=True)


def parse_ts(s):
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def horas(ts):
    t = parse_ts(ts)
    if not t:
        return None
    return max(0.0, (datetime.utcnow() - t).total_seconds() / 3600.0)


def cargar():
    with open("data.json", encoding="utf-8") as fh:
        d = json.load(fh)
    with open("modulos.json", encoding="utf-8") as fh:
        reg = json.load(fh)
    return d, reg["modulos"]


# ---------------- diagnóstico por módulo ----------------

def _fuente_ok(f, limit_h, precio_fn):
    if not isinstance(f, dict) or not f.get("ts"):
        return "FALLA", "sin datos ni timestamp en data.json"
    e = horas(f.get("ts"))
    if e is None:
        return "FALLA", "timestamp ilegible"
    if e > limit_h:
        return "ALERTA", f"desactualizado {e:.1f} h (límite {limit_h} h)"
    if f.get("ok") is False:
        return "ALERTA", f"flag ok=false del último ciclo (fresco {e:.1f} h)"
    n, det = precio_fn(f)
    if not n:
        return "FALLA", f"sin precios legibles: {det}"
    return "OK", f"{det} · fresco {e:.1f} h"


def diag_modulo(m, d, gate_sitio):
    """Devuelve (estado, detalle) para el módulo numerado m."""
    mid = m["id"]
    F = (d.get("fuentes") or {})

    if mid.startswith("vuelos-"):
        key = mid.split("-", 1)[1]
        if key == "jetsmart":
            return _fuente_ok(F.get("jetsmart"), LIMIT_H["vuelos-jetsmart"],
                              lambda f: (len(f.get("combos") or []), f"{len(f.get('combos') or [])} combos"))
        if key == "sky":
            return _fuente_ok(F.get("sky"), LIMIT_H["vuelos-sky"],
                              lambda f: (len(f.get("pairs") or []), f"{len(f.get('pairs') or [])} pares"))
        if key == "latam":
            return _fuente_ok(F.get("latam"), LIMIT_H["vuelos-latam"],
                              lambda f: (len(f.get("combos") or []), f"{len(f.get('combos') or [])} combos"))
        if key == "cocha":
            return _fuente_ok(F.get("cocha"), LIMIT_H["vuelos-cocha"],
                              lambda f: (len(f.get("ventanas") or []), f"{len(f.get('ventanas') or [])} ventanas (mínimos verificados)"))
        if key in ("despegar", "falabella"):
            return _fuente_ok(F.get(key), LIMIT_H["vuelos-" + key],
                              lambda f: (len(f.get("dias") or []), f"{len(f.get('dias') or [])} fechas en grilla"))
        return "NEUTRO", "fuente no inventoryada"

    if mid == "consistencia-otas":
        dp = isinstance(F.get("despegar"), dict) and (F["despegar"].get("dias"))
        fb = isinstance(F.get("falabella"), dict) and (F["falabella"].get("dias"))
        if dp and fb:
            return "OK", "Despegar y Falabella con grillas presentes"
        return "ALERTA", "alguna OTA sin grilla en este ciclo (se re-captura)"

    if mid == "playas":
        pl = F.get("playas") or {}
        if not isinstance(pl, dict) or not pl.get("ts"):
            return "FALLA", "sin datos de playas"
        e = horas(pl["ts"])
        dests = pl.get("destinos") or []
        con_mejor = [x for x in dests if (x.get("mejor") or {}).get("clp")]
        if e is None:
            return "FALLA", "timestamp ilegible"
        if e > LIMIT_H["playas"]:
            return "ALERTA", f"desactualizado {e:.1f} h"
        # Task 26: la tarjeta GIG debe salir del mismo ciclo fresco que las
        # secciones de precios (sync GIG del scraper) — si está desfasada, la
        # re-captura re-sincroniza y elimina la contradicción entre módulos.
        gig = next((x for x in dests if x.get("cod") == "GIG"), None)
        mg = (gig or {}).get("mejor") or {}
        e_gig = horas(mg.get("ts")) if mg.get("ts") else None
        if e_gig is None or e_gig > 0.5:
            return "ALERTA", (f"tarjeta GIG desfasada del ciclo fresco (mejor.ts={mg.get('ts')}) "
                              "— la re-captura re-sincroniza con 'Precios hoy'")
        return ("OK" if len(con_mejor) >= 10 else "ALERTA",
                f"{len(con_mejor)}/{len(dests)} destinos · GIG sincronizado ({e_gig*60:.0f} min) · fresco {e:.1f} h")

    if mid == "cupones":
        cp = d.get("cupones") or {}
        e = horas(cp.get("ts")) if cp.get("ts") else None
        n = len(cp.get("items") or [])
        if e is None:
            return "FALLA", "sin timestamp de cupones"
        if e > LIMIT_H["cupones"]:
            return "ALERTA", f"desactualizado {e/24:.1f} días (límite 7)"
        if not n:
            return "ALERTA", "sin cupones en este ciclo (degradación suave activa)"
        return "OK", f"{n} cupones ({(cp.get('stats') or {}).get('con_codigo', 0)} con código) · fresco {e:.1f} h"

    if mid == "combos":
        cb = d.get("combos") or {}
        if not cb:
            return "ALERTA", "sin sección combos (se recalcula en cada re-captura)"
        e = horas(cb.get("ts")) if cb.get("ts") else None
        return ("OK", f"combos presentes{' · fresco ' + format(e, '.1f') + ' h' if e else ''}")

    if mid == "stays":
        st = d.get("stays") or {}
        e = horas(st.get("ts")) if st.get("ts") else None
        n = len(st.get("destinos") or [])
        if e is None:
            return "ALERTA", "sin datos de alojamientos todavía"
        if e > LIMIT_H["stays"]:
            return "ALERTA", f"desactualizado {e:.1f} h"
        return ("OK" if n else "ALERTA", f"{n} destinos con precio · fresco {e:.1f} h")

    if mid == "noche":
        nc = d.get("noche")
        if isinstance(nc, dict) and nc.get("fecha"):
            return "OK", f"congelamiento del {nc.get('fecha')} · {len(nc.get('minimos') or {})} series"
        return "ALERTA", "sin referencia nocturna aún"

    if mid == "minimos":
        n = len(d.get("minimos") or {})
        return ("OK", f"{n} series con mínimo histórico") if n else ("ALERTA", "sin mínimos")

    if mid == "suscriptores":
        sus = d.get("suscriptores") or {}
        return "OK", f"{sus.get('total', 0)} suscriptos (correos cifrados)"

    if mid == "assets":
        n = len(glob.glob("assets/**/*.jpg", recursive=True) + glob.glob("assets/**/*.png", recursive=True))
        return ("OK", f"{n} imágenes en assets/") if n else ("FALLA", "carpeta assets vacía")

    if mid == "sitio":
        if gate_sitio is None:
            return "NEUTRO", "el gate corre al final de esta reparación"
        return ("OK", "verify.js PASS sobre el dato a publicar") if gate_sitio else ("FALLA", "verify.js FALLÓ")

    # viva-jetsmart / viva-sky: se verifican en la auditoría programada (4×día)
    return "NEUTRO", "se verifica con muestra viva en la próxima auditoría programada"


def barrido(modulos, d, gate_sitio, etiqueta):
    rep = []
    for m in modulos:
        num, mid = m["numero"], m["id"]
        try:
            est, det = diag_modulo(m, d, gate_sitio)
        except Exception as ex:
            est, det = "FALLA", f"error de diagnóstico: {str(ex)[:90]}"
        rep.append({"numero": num, "id": mid, "estado": est, "detalle": det})
        kind = "notice" if est in ("OK", "VERIFICADO") else ("error" if est == "FALLA" else "warning")
        ann(kind, f"[{num} {m['nombre']}] {etiqueta}: {est} — {det}")
    return rep


def correr(cmd, timeout):
    """Corre un comando capturando rc y la cola de salida (para el reporte)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        tail = "\n".join((r.stdout + "\n" + r.stderr).strip().splitlines()[-6:])
        return r.returncode, tail
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT tras {timeout}s"
    except Exception as ex:
        return 1, str(ex)[:200]


def main():
    t0 = time.time()
    d, modulos = cargar()
    trigger = os.environ.get("REPARAR_TRIGGER", "manual")

    # ---------- FASE 0: gate sobre el dato publicado ----------
    rc0, out0 = correr(["node", "verify.js", "index.html", "data.json"], 180)
    gate_publicado = (rc0 == 0)
    ann("notice" if gate_publicado else "warning",
        f"[fase 0] gate sobre el dato PUBLICADO: {'PASS' if gate_publicado else 'FAIL'}")

    # ---------- FASE 1: barrido de diagnóstico ----------
    print(f"\n=== FASE 1 · barrido de {len(modulos)} módulos (dato publicado) ===", flush=True)
    antes = barrido(modulos, d, gate_publicado, "antes")

    # ---------- FASE 2: re-captura completa (con anti-bucle) ----------
    recaptura = True
    motivos = []
    try:
        with open("estado_reparacion.json", encoding="utf-8") as fh:
            last = parse_ts(json.load(fh).get("ts"))
        gap_min = (datetime.utcnow() - last).total_seconds() / 60 if last else 1e9
        if gap_min < COOLDOWN_MIN and gate_publicado:
            recaptura = False
            motivos.append(f"última reparación hace {gap_min:.0f} min (< {COOLDOWN_MIN}) y el dato publicado pasa el gate — anti-bucle")
    except Exception:
        pass
    if not gate_publicado and any(x["estado"] == "FALLA" for x in antes):
        recaptura = True  # hay algo roto de verdad: recapturar pese al cooldown

    comandos = []
    if recaptura:
        print("\n=== FASE 2 · re-captura completa (scraper + cupones) ===", flush=True)
        ann("notice", "[fase 2] re-captura completa de fuentes en curso (scraper.py + fetch_coupons.py)")
        rc, tail = correr([sys.executable, "scraper.py"], SCRAPER_TIMEOUT)
        comandos.append({"cmd": "scraper.py", "rc": rc, "tail": tail[-400:]})
        ann("notice" if rc == 0 else "error", f"[fase 2] scraper.py → rc={rc}")
        rc2, tail2 = correr([sys.executable, "fetch_coupons.py"], COUPONS_TIMEOUT)
        comandos.append({"cmd": "fetch_coupons.py", "rc": rc2, "tail": tail2[-400:]})
        ann("notice" if rc2 == 0 else "warning", f"[fase 2] fetch_coupons.py → rc={rc2} (forzado, sin gate 6 h)")
        with open("data.json", encoding="utf-8") as fh:
            d = json.load(fh)
    else:
        print("\n=== FASE 2 · re-captura OMITIDA (anti-bucle) ===", flush=True)
        for mo in motivos:
            ann("notice", f"[fase 2] omitida: {mo}")

    # ---------- FASE 3: re-barrido + gate final ----------
    print("\n=== FASE 3 · re-barrido + GATE final ===", flush=True)
    rc3, _ = correr(["node", "verify.js", "index.html", "data.json"], 180)
    gate_final = (rc3 == 0)
    despues = barrido(modulos, d, gate_final, "después")
    ann("notice" if gate_final else "error",
        f"[fase 3] GATE verify.js final: {'PASS — el dato queda verificado para publicar' if gate_final else 'FAIL — no se publica'}")

    # ---------- FASE 4: reportes ----------
    acciones = []
    for a, b in zip(antes, despues):
        accion = "sin cambio" if a["estado"] == b["estado"] else f"{a['estado']} → {b['estado']}"
        acciones.append({"numero": a["numero"], "id": a["id"],
                         "antes": a["estado"], "despues": b["estado"],
                         "detalle": b["detalle"], "accion": accion})
    rep = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "ts_stgo": datetime.now(timezone.utc - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"),
        "trigger": trigger,
        "gate_publicado": ("PASS" if gate_publicado else "FAIL"),
        "gate_final": ("PASS" if gate_final else "FAIL"),
        "recaptura": recaptura,
        "motivos_recaptura_omitida": motivos,
        "comandos": comandos,
        "secs": round(time.time() - t0, 1),
        "modulos": acciones,
        "resumen": f"{sum(1 for x in acciones if x['despues'] in ('OK', 'VERIFICADO'))}/{len(acciones)} módulos OK tras la reparación",
    }
    with open("reparacion.json", "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
    with open("estado_reparacion.json", "w", encoding="utf-8") as fh:
        json.dump({"ts": rep["ts"], "trigger": trigger}, fh, ensure_ascii=False)

    n_ok = sum(1 for x in acciones if x["despues"] in ("OK", "VERIFICADO"))
    n_falla = sum(1 for x in acciones if x["despues"] == "FALLA")
    print(f"\nAUTO-REPARACIÓN {rep['resumen']} · gate final {'PASS' if gate_final else 'FAIL'} "
          f"({rep['secs']}s, recaptura={'sí' if recaptura else 'no'})", flush=True)
    if n_falla:
        for x in acciones:
            if x["despues"] == "FALLA":
                ann("error", f"[{x['numero']}] irreparable automáticamente: {x['detalle']}")
    sys.exit(0 if gate_final else 1)


if __name__ == "__main__":
    main()
