#!/usr/bin/env python3
"""Escáner LOCAL de Falabella/Despegar — corre en TU computador (IP residencial).

¿Por qué local? DataDome (el WAF de estas OTAs) tolera bien las IPs residenciales
pero banea las de servidores (GitHub Actions/Azure). Corriendo esto en tu PC el
challenge pasa prácticamente siempre y los precios quedan frescos en el dashboard.

USO:
    pip install camoufox && python -m camoufox fetch
    python scan_otas_local.py            # actualiza data.json local
    python scan_otas_local.py --push     # actualiza y commitea/pushea al repo

Tarda ~3-5 min (4 páginas con espera paciente). Seguro de correr seguido;
respeta un mínimo de 10 min entre corridas para no levantar sospechas.
"""
import argparse
import json
import os
import sys
import time
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data.json")

sys.path.insert(0, BASE)
import fetch_otas  # noqa: E402


def now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="commitea y pushea data.json al repo")
    args = ap.parse_args()

    print("== Escáner OTA local (Camoufox stealth) ==")
    print("Abriendo navegador... no cierres la ventana si aparece.")
    otas = fetch_otas.fetch_otas(log=lambda m: print(m, flush=True))

    fa_ok = fetch_otas.ok(otas["falabella"]["dias"])
    de_ok = fetch_otas.ok(otas["despegar"]["dias"])
    print(f"\nResultado: Falabella {fa_ok}/2 lecturas · Despegar {de_ok}/2 lecturas")

    if not (fa_ok or de_ok):
        print("Sin precios: nada que publicar. Intenta de nuevo en 15+ minutos.")
        sys.exit(1)

    with open(DATA, encoding="utf8") as fh:
        live = json.load(fh)
    fuentes = live.setdefault("fuentes", {})
    if fa_ok:
        fuentes["falabella"] = {"ts": now_iso(), "ok": True,
            "nota": "lectura local verificada (IP residencial) — precio de referencia",
            **otas["falabella"]}
    if de_ok:
        fuentes["despegar"] = {"ts": now_iso(), "ok": True,
            "nota": "lectura local verificada (IP residencial) — precio de referencia",
            **otas["despegar"]}
    live["ota_ts"] = now_iso()
    live["last_price_change"] = now_iso()
    live["updated"] = now_iso()

    tmp = DATA + ".tmp"
    with open(tmp, "w", encoding="utf8") as fh:
        json.dump(live, fh, ensure_ascii=False)
    os.replace(tmp, DATA)
    print(f"data.json actualizado ({DATA})")

    if args.push:
        import subprocess
        subprocess.run(["git", "-C", BASE, "add", "data.json"], check=True)
        msg = f"OTA local verificado {now_iso()}: Falabella {fa_ok}/2 · Despegar {de_ok}/2"
        subprocess.run(["git", "-C", BASE, "commit", "-m", msg], check=False)
        subprocess.run(["git", "-C", BASE, "pull", "--rebase"], check=False)
        subprocess.run(["git", "-C", BASE, "push"], check=True)
        print("Pusheado — en ~1 min el dashboard muestra los precios frescos.")


if __name__ == "__main__":
    main()
