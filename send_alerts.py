#!/usr/bin/env python3
"""Alertas de bajada de precio por correo. Corre tras scraper.py en el workflow.

Los destinatarios viven CIFRADOS en subscribers.enc.json (blobs RSA-OAEP);
se descifran en memoria con el secreto ALERT_RSA_PRIV solo para enviar.
Si SMTP_HOST/SMTP_USER/SMTP_PASS no están configurados, la alerta queda
pendiente hasta que se configuren credenciales. En los logs los correos
siempre aparecen enmascarados (j•••m).
"""
import json
import os
import smtplib
import sys
from email.header import Header
from email.mime.text import MIMEText

DATA = "data.json"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notificaciones import cargar_emails, mask  # noqa: E402


def main():
    d = json.load(open(DATA, encoding="utf8"))
    if not d.get("email_pending"):
        print("Sin alertas pendientes."); return
    subs = cargar_emails()
    last_sent = d.get("alerts_sent_ts", "")
    nuevos = [a for a in (d.get("alerts") or []) if a["ts"] > last_sent]
    if not nuevos:
        d["email_pending"] = False
        json.dump(d, open(DATA, "w"), ensure_ascii=False)
        print("email_pending sin alertas nuevas: limpio.")
        return
    max_ts = max(a["ts"] for a in nuevos)
    if not subs:
        d["alerts_sent_ts"] = max_ts; d["email_pending"] = False
        json.dump(d, open(DATA, "w"), ensure_ascii=False)
        print("Hay bajadas pero NADIE suscrito todavía: marcadas como notificadas.")
        return
    host = (os.environ.get("SMTP_HOST") or "").strip()
    user = (os.environ.get("SMTP_USER") or "").strip()
    # Gmail muestra la contraseña de aplicación en bloques de 4 separados por
    # espacios: se eliminan automáticamente por si se copió tal cual.
    pwd = (os.environ.get("SMTP_PASS") or "").replace(" ", "").replace("\u00a0", "").strip()
    port = int(os.environ.get("SMTP_PORT") or "587")
    if not (host and user and pwd):
        print(f"SMTP sin configurar: {len(nuevos)} alerta(s) quedan pendientes para "
              f"{len(subs)} suscrito(s) ({', '.join(mask(s) for s in subs[:5])}).")
        return
    lines = ["Bajada de precios detectada en vuelos Santiago ⇄ Río de Janeiro (ene–feb 2027):", ""]
    for a in nuevos:
        lines.append(f"• {a['serie']}: ${a['antes']:,} → ${a['ahora']:,} "
                     f"(bajó ${a['baja']:,} / −{a['pct']}%)")
    lines += ["", "Ver el panel actualizado: https://xacox01.github.io/vuelos-rio-live/", "",
              "Compara siempre en las fuentes oficiales antes de comprar: los precios "
              "de vuelos son dinámicos.", "",
              "Para desuscribirte: en el panel, sección Alertas, botón «Darme de baja» "
              "(mismo correo con el que te suscribiste).",
              "Tu correo está guardado cifrado y nunca se muestra en el listado público."]
    msg_body = "\n".join(lines)
    ok = 0
    for dest in subs:
        try:
            if port == 465:
                srv = smtplib.SMTP_SSL(host, port, timeout=30)
            else:
                srv = smtplib.SMTP(host, port, timeout=30)
            with srv as s:
                if port != 465:
                    s.starttls()
                s.login(user, pwd)
                # MIME correcto: el asunto/cuerpo llevan acentos (Río) y ⇄;
                # un header crudo no-ASCII hace que smtplib falle con 'ascii' codec.
                msg = MIMEText(msg_body, "plain", "utf-8")
                msg["Subject"] = Header(
                    "[vuelos-rio-live] Baja de precios a Río de Janeiro", "utf-8")
                msg["From"] = user
                msg["To"] = dest
                s.sendmail(user, [dest], msg.as_string())
            ok += 1
            print(f"enviado → {mask(dest)}")
        except Exception as e:
            print(f"FALLO envío a {mask(dest)}: {str(e)[:80]}")
    if ok:
        d["alerts_sent_ts"] = max_ts; d["email_pending"] = False
        json.dump(d, open(DATA, "w"), ensure_ascii=False)
        print(f"OK: {ok}/{len(subs)} correos enviados.")
    else:
        print("Ningún envío exitoso: se reintentará el próximo ciclo.")


if __name__ == "__main__":
    main()
