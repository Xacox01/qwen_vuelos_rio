#!/usr/bin/env python3
"""bus_alertas.py — bus de suscripciones AUTO-ROTANTE (webhook.site).

Historia (dueño): el bus original (webhook.site/4fd4477f-…) murió el 18-oct
porque los endpoints gratuitos de webhook.site EXPIRAN A LOS 7 DÍAS. Como el
front-end publica con mode:"no-cors" y notificaciones.py solo avisaba en el
log, las suscripciones dejaron de llegar en silencio en AMBOS sitios.

Este módulo hace que el bus nunca vuelva a morir en silencio:

  1. ensure_bus(): comprueba que el endpoint de alertas.cfg.json esté vivo.
     Si murió (404/5xx), crea OTRO endpoint con CORS abierto, actualiza
     alertas.cfg.json y avisa con ::notice::. El front-end lee la cfg sin
     caché en cada clic, así que siempre publica al endpoint vigente.
  2. Cada repo tiene SU PROPIO endpoint: antes ambos sitios compartían bus y
     quien refrescaba primero (rio-live, cada 5 min) se llevaba las
     suscripciones del otro (qwen, cada 10). Ahora no hay carrera.

Uso:
  python3 bus_alertas.py            # asegura bus vivo (rota si murió); exit 0
  python3 bus_alertas.py --check    # solo informa (sin rotar)

La rotación es segura: los suscriptores ya registrados viven cifrados en
subscribers.enc.json (no dependen del bus); solo las solicitudes EN VUELO
durante la muerte del endpoint podrían perderse (ventana de minutos cada
7 días).
"""
import json
import sys
import time
import urllib.error
import urllib.request

CFG = "alertas.cfg.json"
API = "https://webhook.site"

NUEVO = {
    "default_status": 200,
    "default_content": "ok",
    "cors": {
        "allowed_origins": ["*"],
        "allowed_methods": ["GET,POST,PUT,DELETE,OPTIONS"],
        "allowed_headers": ["*"],
        "expose_headers": ["*"],
    },
}


def http(url, method="GET", data=None, timeout=20):
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json",
                 "User-Agent": "radar-rio-bus/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def leer_cfg():
    with open(CFG, encoding="utf8") as f:
        return json.load(f)


def escribir_cfg(cfg):
    with open(CFG, "w", encoding="utf8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)


def endpoint_token(hook):
    """uuid del endpoint desde la URL de cfg ('https://webhook.site/<uuid>')."""
    return (hook or "").rstrip("/").split("/")[-1]


def bus_vivo(token):
    """True si el endpoint existe y responde."""
    if not token or len(token) < 8:
        return False
    try:
        http(f"{API}/token/{token}", timeout=15)
        return True
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):          # rate limit momentáneo ≠ muerto
            return True
        return False                       # 404 = expiró/borrado
    except Exception:
        return False                       # red caída: tratarlo como muerto
                                           # (ensure lo re-crea solo si de verdad
                                           #  la API está disponible)


def crear_endpoint():
    d = json.loads(http(f"{API}/token", method="POST",
                        data=json.dumps(NUEVO).encode(), timeout=25).decode())
    return d["uuid"], d.get("expires_at") or ""


def ensure_bus(solo_check=False):
    """Devuelve dict de estado; rota el endpoint si el actual está muerto."""
    cfg = leer_cfg()
    hook = (cfg.get("webhook") or "").strip()
    token = endpoint_token(hook)
    vivo = bus_vivo(token)
    estado = {"bus_vivo": vivo, "bus_uuid": token[-8:] if token else "",
              "rotado": False, "webhook": hook}

    if solo_check or vivo:
        return estado

    # muerto o mal configurado → rotar
    nuevo, expira = "", ""
    for i in (1, 2, 3):
        try:
            nuevo, expira = crear_endpoint()
            break
        except Exception as e:
            print(f"bus_alertas: creación de endpoint falló (intento {i}): "
                  f"{str(e)[:80]}", flush=True)
            time.sleep(5 * i)
    if not nuevo:
        print("::error::[alertas] bus de suscripciones MUERTO y no se pudo "
              "rotar (webhook.site inaccesible). Las nuevas suscripciones no "
              "llegan hasta que rote; los suscriptores actuales no se pierden.",
              flush=True)
        estado["bus_vivo"] = False
        return estado

    cfg["webhook"] = f"{API}/{nuevo}"
    cfg["bus_rotado_ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    cfg["bus_expira"] = expira
    cfg["nota_bus"] = ("endpoint auto-rotante: expira en 7 días y "
                       "bus_alertas.py (refresh) crea el siguiente solo")
    escribir_cfg(cfg)
    print(f"::notice::[alertas] bus rotado: …{token[-8:]} → …{nuevo[-8:]} "
          f"(expira {expira[:10] if expira else 'en 7 días'}). "
          "El front-end toma la cfg nueva sin caché en el próximo reload.",
          flush=True)
    estado.update({"bus_vivo": True, "bus_uuid": nuevo[-8:],
                   "rotado": True, "webhook": cfg["webhook"]})
    return estado


def main():
    solo = "--check" in sys.argv
    e = ensure_bus(solo_check=solo)
    tag = "CHECK" if solo else "ENSURE"
    print(f"bus_alertas[{tag}]: vivo={e['bus_vivo']} uuid=…{e['bus_uuid']} "
          f"rotado={e['rotado']}", flush=True)
    # exit 0 SIEMPRE: la publicación del sitio no puede depender del bus;
    # verify.js decide si el bus vivo es bloqueante (cfg ya quedó actualizada)
    return 0


if __name__ == "__main__":
    sys.exit(main())
