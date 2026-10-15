#!/usr/bin/env python3
"""Módulo de notificaciones por correo — privacy-first.

Flujo completo:
  1. El visitante escribe su correo en el sitio; el NAVEGADOR lo cifra con
     RSA-2048/OAEP-SHA256 (clave pública en alertas.cfg.json) y publica el
     blob cifrado en un bus de mensajes (webhook.site). El correo NUNCA viaja
     ni se guarda en claro.
  2. Este proceso (corre en Actions cada ciclo, ANTES de publicar) descarga
     los blobs nuevos del bus, los descifra con la clave privada (secreto
     ALERT_RSA_PRIV), valida el correo y aplica ALTA/BAJA en el registro
     cifrado subscribers.enc.json (el repo público nunca contiene correos).
  3. Publica en data.json solo datos ENMASCARADOS: total de suscritos y
     muestras con la forma j•••m (primera y última letra del correo).
  4. send_alerts.py reutiliza cargar_emails() para notificar las bajadas.

Sin la clave privada configurada NO procesa NI borra del bus (evita perder
suscripciones): solo avisa y sale limpio.
"""
import base64
import json
import os
import re
import sys
import time
import urllib.request
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

CFG = "alertas.cfg.json"
ENC = "subscribers.enc.json"
EST = "estado_alertas.json"
DATA = "data.json"
EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]{2,}$")


def mask(correo):
    """Enmascara un correo: solo primera y última letra visibles (j•••m)."""
    c = (correo or "").strip()
    if "@" not in c or len(c) < 5:
        return "•••"
    return c[0] + "•••" + c[-1]


def http(url, method="GET", data=None, timeout=30):
    req = urllib.request.Request(url, data=data, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def load_priv():
    pem = (os.environ.get("ALERT_RSA_PRIV") or "").strip()
    if not pem:
        return None
    try:
        return serialization.load_pem_private_key(pem.encode(), password=None)
    except Exception as e:
        print(f"notificaciones: ALERT_RSA_PRIV inválido ({str(e)[:60]})", flush=True)
        return None


def decrypt_blob(priv, b64):
    raw = base64.b64decode(b64)
    payload = priv.decrypt(raw, padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    return json.loads(payload.decode("utf8"))


def cargar_emails():
    """Lista de correos suscritos (descifra el registro; uso de send_alerts.py)."""
    priv = load_priv()
    if not priv:
        return []
    try:
        reg = json.load(open(ENC, encoding="utf8"))
    except Exception:
        return []
    out = set()
    for it in reg.get("items", []):
        try:
            p = decrypt_blob(priv, it["blob"])
            m = (p.get("e") or "").strip().lower()
            if EMAIL_RE.match(m):
                out.add(m)
        except Exception:
            continue
    return sorted(out)


def main():
    try:
        cfg = json.load(open(CFG, encoding="utf8"))
    except Exception as e:
        print(f"notificaciones: sin config ({str(e)[:50]}) — omitido", flush=True)
        return
    hook = (cfg.get("webhook") or "").rstrip("/")
    if not hook:
        print("notificaciones: cfg sin webhook — omitido", flush=True)
        return
    token = hook.split("/")[-1]
    api = f"https://webhook.site/token/{token}"

    priv = load_priv()
    if not priv:
        print("notificaciones: ALERT_RSA_PRIV sin configurar — no proceso el bus "
              "(las suscripciones quedan en cola cifrada, nada se pierde)", flush=True)
        return

    try:
        page = json.loads(http(f"{api}/requests?sorting=oldest&page=1&per_page=50"))
    except Exception as e:
        print(f"notificaciones: bus no disponible ({str(e)[:60]}) — reintenta el "
              "próximo ciclo", flush=True)
        return
    entrantes = page.get("data") or []

    # registro cifrado previo → mapa email→índice
    reg = {"v": 1, "items": [], "ts": "", "nota": "blobs RSA-OAEP; sin correos en claro"}
    if os.path.exists(ENC):
        try:
            reg = json.load(open(ENC, encoding="utf8"))
        except Exception:
            reg = {"v": 1, "items": [], "ts": "", "nota": "blobs RSA-OAEP; sin correos en claro"}
    items = reg.get("items", [])
    vistos = set()
    if os.path.exists(EST):
        try:
            vistos = set((json.load(open(EST, encoding="utf8"))).get("vistos", [])[-400:])
        except Exception:
            vistos = set()

    def mapa():
        m = {}
        for i, it in enumerate(items):
            try:
                p = decrypt_blob(priv, it["blob"])
                e = (p.get("e") or "").strip().lower()
                if EMAIL_RE.match(e):
                    m[e] = i
            except Exception:
                continue
        return m

    n_alta = n_baja = n_err = n_dup = 0
    procesados = []
    for r in entrantes:
        rid = r.get("uuid") or ""
        contenido = (r.get("content") or "").strip()
        # borrar del bus SIEMPRE tras leer (procesado o basura; ya no sirve)
        try:
            http(f"{api}/request/{rid}", method="DELETE", timeout=15)
        except Exception:
            pass
        if not rid or rid in vistos:
            continue
        m = re.match(r"^v1\|([A-Za-z0-9+/=]+)$", contenido)
        if not m:
            n_err += 1
            continue
        try:
            p = decrypt_blob(priv, m.group(1))
        except Exception:
            n_err += 1
            continue
        correo = (p.get("e") or "").strip().lower()
        if not EMAIL_RE.match(correo):
            n_err += 1
            continue
        acc = "baja" if p.get("a") == "baja" else "alta"
        idx = mapa().get(correo)
        if acc == "alta":
            if idx is not None:
                items[idx] = {"blob": m.group(1), "ts": int(time.time())}
                n_dup += 1
            else:
                items.append({"blob": m.group(1), "ts": int(time.time())})
                n_alta += 1
        else:
            if idx is not None:
                items.pop(idx)
                n_baja += 1
        procesados.append(rid)

    # 1) registro cifrado a disco
    reg["items"] = items
    reg["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(ENC, "w", encoding="utf8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=1)

    # 2) data.json: SOLO enmascarados
    if os.path.exists(DATA):
        try:
            d = json.load(open(DATA, encoding="utf8"))
            muestras = []
            for it in items[-8:]:
                try:
                    muestras.append(mask(decrypt_blob(priv, it["blob"])["e"]))
                except Exception:
                    continue
            d["suscriptores"] = {"total": len(items), "muestras": muestras,
                                 "ts": reg["ts"]}
            with open(DATA, "w", encoding="utf8") as f:
                json.dump(d, f, ensure_ascii=False)
        except Exception as e:
            print(f"notificaciones: data.json no actualizado ({str(e)[:60]})", flush=True)

    # 3) estado de lectura
    est = {"vistos": [], "ts": reg["ts"]}
    if os.path.exists(EST):
        try:
            est["vistos"] = (json.load(open(EST, encoding="utf8"))).get("vistos", [])
        except Exception:
            pass
    est["vistos"] = (est["vistos"] + procesados)[-400:]
    est["ts"] = reg["ts"]
    with open(EST, "w", encoding="utf8") as f:
        json.dump(est, f, ensure_ascii=False, indent=1)

    print(f"notificaciones: altas {n_alta} · bajas {n_baja} · repetidas {n_dup} · "
          f"inválidas {n_err} · total suscritos {len(items)}", flush=True)
    if n_alta or n_baja:
        print("notificaciones: muestras publicadas →",
              ", ".join(mask(decrypt_blob(priv, it["blob"])["e"]) for it in items[-5:]),
              flush=True)


if __name__ == "__main__":
    sys.exit(main())
