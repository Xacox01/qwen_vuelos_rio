#!/usr/bin/env python3
"""Módulo de notificaciones por correo — privacy-first.

Flujo completo:
  1. El visitante escribe su correo en el sitio; el NAVEGADOR lo cifra con
     RSA-2048/OAEP-SHA256 (clave pública en alertas.cfg.json) y publica el
     blob cifrado en un bus de mensajes (webhook.site). El correo NUNCA viaja
     ni se guarda en claro.
  2. Este proceso (corre en Actions cada ciclo, ANTES de publicar) descarga
     los blobs nuevos del bus (asegurado vivo por bus_alertas.py en el paso
     anterior del workflow), los descifra con la clave privada (secreto
     ALERT_RSA_PRIV), valida el correo y aplica ALTA/BAJA en el registro
     cifrado subscribers.enc.json (el repo público nunca contiene correos).
     Un blob solo se borra del bus tras procesarse con éxito; los blobs que
     no descifran se reintentan 10 ciclos antes de soltarse (lección del
     bus muerto del 18-oct: nada de pérdidas silenciosas).
  3. Publica en data.json solo datos ENMASCARADOS: total de suscritos y
     muestras con la forma j•••m (primera y última letra del correo), más
     email_cfg (booleanos rsa/bus) que consume el panel de monitoreo.
  4. send_alerts.py reutiliza cargar_emails() para notificar las bajadas.

Sin la clave privada configurada NO procesa NI borra del bus (evita perder
suscripciones): avisa con ::error:: (visible en la página del run) y sale
limpio.
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


def _email_cfg(rsa, bus, cfg=None):
    """Escribe data.json→email_cfg (parte de notificaciones: rsa + bus).
    send_alerts.py añade después la parte smtp. Nunca incluye secretos."""
    if not os.path.exists(DATA):
        return
    try:
        d = json.load(open(DATA, encoding="utf8"))
        ec = d.get("email_cfg") or {}
        ec["rsa"] = bool(rsa)
        if bus is not None:
            ec["bus"] = bool(bus)
            if cfg:
                u = (cfg.get("webhook") or "").rstrip("/").split("/")[-1]
                ec["bus_uuid"] = u[-8:]
                ec["bus_rotado"] = cfg.get("bus_rotado_ts", "")
        ec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        d["email_cfg"] = ec
        with open(DATA, "w", encoding="utf8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception as e:
        print(f"notificaciones: email_cfg no actualizado ({str(e)[:60]})",
              flush=True)


def rsa_disponible():
    """True si la clave privada está configurada (distingue 'sin suscriptores'
    de 'clave ausente: correos bloqueados'). Uso de send_alerts.py."""
    return load_priv() is not None


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
        print("::error::[alertas] ALERT_RSA_PRIV sin configurar en ESTE repo — "
              "no puedo descifrar suscriptores ni enviar correos de este sitio. "
              "Configúralo en Settings → Secrets and variables → Actions "
              "(las suscripciones quedan en cola cifrada, nada se pierde).",
              flush=True)
        _email_cfg(rsa=False, bus=None)
        return

    try:
        page = json.loads(http(f"{api}/requests?sorting=oldest&page=1&per_page=50"))
    except Exception as e:
        print(f"::error::[alertas] bus de suscripciones no disponible "
              f"({str(e)[:60]}) incluso tras la rotación de bus_alertas.py — "
              "las nuevas suscripciones no llegan este ciclo; los suscriptores "
              "actuales no se pierden. Reintenta el próximo ciclo.", flush=True)
        _email_cfg(rsa=True, bus=False)
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

    # reintento de blobs indescifrables: rid → nº de ciclos llevados
    fallidos = {}
    try:
        fallidos = (json.load(open(EST, encoding="utf8"))).get("fallidos", {})
    except Exception:
        fallidos = {}

    def soltar(rid):
        """Borra del bus SOLO cuando ya no es re-procesable."""
        try:
            http(f"{api}/request/{rid}", method="DELETE", timeout=15)
        except Exception:
            pass

    n_alta = n_baja = n_err = n_dup = 0
    procesados = []
    for r in entrantes:
        rid = r.get("uuid") or ""
        contenido = (r.get("content") or "").strip()
        if not rid:
            continue
        if rid in vistos:
            soltar(rid)            # ya procesado en un ciclo anterior
            continue
        m = re.match(r"^v1\|([A-Za-z0-9+/=]+)$", contenido)
        if not m:
            soltar(rid)            # basura: no es un blob nuestro
            n_err += 1
            continue
        try:
            p = decrypt_blob(priv, m.group(1))
        except Exception:
            # NO borrar todavía: podría descifrar con la clave correcta más
            # adelante (p.ej. ALERT_RSA_PRIV recién corregido). Tras 10
            # ciclos se suelta para no acumular.
            fallidos[rid] = int(fallidos.get(rid, 0)) + 1
            if fallidos[rid] >= 10:
                soltar(rid)
                fallidos.pop(rid, None)
            n_err += 1
            continue
        correo = (p.get("e") or "").strip().lower()
        if not EMAIL_RE.match(correo):
            soltar(rid)
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
        fallidos.pop(rid, None)
        procesados.append(rid)
        soltar(rid)                # borrar SOLO tras procesar con éxito

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

    # 3) estado de lectura (vistos + reintento de blobs indescifrables)
    est = {"vistos": [], "ts": reg["ts"], "fallidos": {}}
    if os.path.exists(EST):
        try:
            prev = json.load(open(EST, encoding="utf8"))
            est["vistos"] = prev.get("vistos", [])
            est["fallidos"] = prev.get("fallidos", {})
        except Exception:
            pass
    est["fallidos"] = fallidos
    est["vistos"] = (est["vistos"] + procesados)[-400:]
    est["ts"] = reg["ts"]
    with open(EST, "w", encoding="utf8") as f:
        json.dump(est, f, ensure_ascii=False, indent=1)

    # 4) email_cfg para el panel de monitoreo (booleanos, nunca secretos)
    _email_cfg(rsa=True, bus=True, cfg=cfg)

    print(f"notificaciones: altas {n_alta} · bajas {n_baja} · repetidas {n_dup} · "
          f"inválidas {n_err} · total suscritos {len(items)}", flush=True)
    if n_alta or n_baja:
        print("notificaciones: muestras publicadas →",
              ", ".join(mask(decrypt_blob(priv, it["blob"])["e"]) for it in items[-5:]),
              flush=True)


if __name__ == "__main__":
    sys.exit(main())
