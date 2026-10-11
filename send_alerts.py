#!/usr/bin/env python3
"""Alertas de bajada de precio por correo. Corre tras scraper.py en el workflow.
Si SMTP_HOST/SMTP_USER/SMTP_PASS no están configurados, avisa y deja pendiente."""
import json, os, smtplib

DATA = "data.json"
SUBS = "subscribers.json"

def main():
    d = json.load(open(DATA, encoding="utf8"))
    if not d.get("email_pending"):
        print("Sin alertas pendientes."); return
    subs = json.load(open(SUBS, encoding="utf8")) if os.path.exists(SUBS) else []
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
        print("Hay bajadas pero NADIE suscrito todavía: marcadas como notificadas en el panel.")
        return
    host, user, pwd = os.environ.get("SMTP_HOST"), os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS")
    if not (host and user and pwd):
        print("SMTP sin configurar: alerta queda pendiente hasta configurar credenciales.")
        return
    lines = ["Baja de precios detectada en vuelos Santiago → Río de Janeiro (ene–feb 2027):", ""]
    for a in nuevos:
        lines.append(f"• {a['serie']}: ${a['antes']:,} → ${a['ahora']:,} (bajó ${a['baja']:,} / −{a['pct']}%)")
    lines += ["", "Ver el panel actualizado: https://xacox01.github.io/vuelos-rio-live/",
              "(espejo: https://xacox01.github.io/qwen_vuelos_rio/)", "",
              "Para desuscribirte: abre un issue 'BAJA tucorreo@x.com' en https://github.com/Xacox01/vuelos-rio-live/issues"]
    msg_body = "\n".join(lines)
    ok = 0
    for dest in subs:
        try:
            with smtplib.SMTP(host, 587, timeout=30) as s:
                s.starttls(); s.login(user, pwd)
                s.sendmail(user, [dest],
                           f"From: {user}\r\nTo: {dest}\r\nSubject: [vuelos-rio-live] Baja de precios a Rio\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{msg_body}")
            ok += 1
            print(f"enviado → {dest}")
        except Exception as e:
            print(f"FALLO envío a {dest}: {str(e)[:80]}")
    if ok:
        d["alerts_sent_ts"] = max_ts; d["email_pending"] = False
        json.dump(d, open(DATA, "w"), ensure_ascii=False)
        print(f"OK: {ok}/{len(subs)} correos enviados.")
    else:
        print("Ningún envío exitoso: se reintentará el próximo ciclo.")

if __name__ == "__main__":
    main()
