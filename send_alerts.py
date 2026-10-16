#!/usr/bin/env python3
"""Alertas de bajada de precio por correo (HTML con identidad Radar Río).
Corre tras notificaciones.py en el workflow, antes de publicar.

Los destinatarios viven CIFRADOS en subscribers.enc.json (blobs RSA-OAEP);
se descifran en memoria con el secreto ALERT_RSA_PRIV solo para enviar.
Si SMTP_HOST/SMTP_USER/SMTP_PASS no están configurados, la alerta queda
pendiente hasta que se configuren credenciales. En los logs los correos
siempre aparecen enmascarados (j•••m).

Diseño del correo: patrones Cerberus (github.com/TedGoas/Cerberus, MIT) —
tablas de 600px, CSS inline, botón bulletproof, compatible Gmail/Outlook,
con logo y paleta oficial del sitio.
"""
import json
import os
import smtplib
import sys
from datetime import datetime, timezone
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

DATA = "data.json"
SITE = "https://xacox01.github.io/vuelos-rio-live/"
LOGO_BLANCO = SITE + "assets/brand/logo-blanco-512.png"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notificaciones import cargar_emails, mask  # noqa: E402

NARANJA = "ff690f"  # color oficial del botón (bgcolor sin # para clientes antiguos)

def clp(n) -> str:
    return "${:,.0f}".format(n).replace(",", ".")


def email_text(alertas) -> str:
    lines = ["Bajada de precios detectada en vuelos Santiago ⇄ Río de Janeiro (ene–feb 2027):", ""]
    for a in alertas:
        lines.append(f"• {a['serie']}: {clp(a['antes'])} → {clp(a['ahora'])} "
                     f"(bajó {clp(a['baja'])} / −{a['pct']}%)")
    lines += ["", f"Ver el panel actualizado: {SITE}", "",
              "Compara siempre en las fuentes oficiales antes de comprar: los precios "
              "de vuelos son dinámicos.", "",
              "Para desuscribirte: en el panel, sección Alertas, botón «Darme de baja» "
              "(mismo correo con el que te suscribiste).",
              "Tu correo está guardado cifrado y nunca se muestra en el listado público."]
    return "\n".join(lines)


def email_html(alertas) -> str:
    """Correo HTML con identidad Radar Río (patrones Cerberus fluid)."""
    hoy = datetime.now(timezone.utc).strftime("%d-%m-%Y %H:%M UTC")
    first = alertas[0]
    preheader = f"{first['serie']}: {clp(first['antes'])} → {clp(first['ahora'])} (−{first['pct']}%)"

    filas = []
    for a in alertas[:8]:
        filas.append(f"""
      <tr>
        <td style="padding:12px 16px;border-bottom:1px solid #e3e7ec;font-family:Arial,sans-serif;font-size:14px;color:#1a2b49;font-weight:bold;">{a['serie']}</td>
        <td style="padding:12px 8px;border-bottom:1px solid #e3e7ec;font-family:Arial,sans-serif;font-size:14px;color:#8a97ab;text-decoration:line-through;text-align:right;white-space:nowrap;">{clp(a['antes'])}</td>
        <td style="padding:12px 8px;border-bottom:1px solid #e3e7ec;font-family:Arial,sans-serif;font-size:15px;color:#073b9e;font-weight:bold;text-align:right;white-space:nowrap;">{clp(a['ahora'])}</td>
        <td style="padding:12px 16px;border-bottom:1px solid #e3e7ec;text-align:right;">
          <span style="background:#fff1e5;color:#c24f00;font-family:Arial,sans-serif;font-size:12px;font-weight:bold;padding:4px 10px;border-radius:999px;white-space:nowrap;">&minus;{a['pct']}%</span>
        </td>
      </tr>""")
    mas = ""
    if len(alertas) > 8:
        mas = (f"<p style=\"margin:12px 0 0;font-family:Arial,sans-serif;font-size:13px;"
               f"color:#5e6d85;\">Y {len(alertas) - 8} bajada(s) más en el panel…</p>")

    return f"""<!DOCTYPE html>
<html lang="es" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>Bajada de precios · Radar Río</title>
<!--[if mso]><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml><![endif]-->
</head>
<body style="margin:0;padding:0;background:#f6f7f9;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{preheader}&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f6f7f9;">
<tr><td align="center" style="padding:24px 12px;">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:100%;">

    <!-- HEADER con logo oficial -->
    <tr><td bgcolor="#0a4fd9" align="center" style="background:#0a4fd9;border-radius:14px 14px 0 0;padding:26px 24px 20px;">
      <img src="{LOGO_BLANCO}" width="64" height="64" alt="Radar Río" style="display:block;border:0;">
      <div style="font-family:Arial,sans-serif;font-size:21px;font-weight:bold;color:#ffffff;letter-spacing:1px;margin-top:10px;">RADAR R&Iacute;O</div>
      <div style="font-family:Arial,sans-serif;font-size:12px;color:#bbd3f5;letter-spacing:2px;margin-top:4px;">ESCANEO EN VIVO &middot; MEJOR PRECIO</div>
    </td></tr>

    <!-- CUERPO -->
    <tr><td bgcolor="#ffffff" style="background:#ffffff;padding:28px 28px 8px;">
      <h1 style="margin:0 0 6px;font-family:Arial,sans-serif;font-size:22px;color:#1a2b49;">Bajaron los precios</h1>
      <p style="margin:0 0 18px;font-family:Arial,sans-serif;font-size:14px;color:#5e6d85;">Detectamos estas bajadas en vuelos Santiago &hArr; R&iacute;o de Janeiro (ene&ndash;feb 2027) &middot; {hoy}</p>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid #e3e7ec;border-radius:10px;">
        <tr style="background:#eef3fe;">
          <th align="left" style="padding:10px 16px;font-family:Arial,sans-serif;font-size:12px;color:#5e6d85;text-transform:uppercase;letter-spacing:.5px;">Ruta</th>
          <th align="right" style="padding:10px 8px;font-family:Arial,sans-serif;font-size:12px;color:#5e6d85;text-transform:uppercase;letter-spacing:.5px;">Antes</th>
          <th align="right" style="padding:10px 8px;font-family:Arial,sans-serif;font-size:12px;color:#5e6d85;text-transform:uppercase;letter-spacing:.5px;">Ahora</th>
          <th align="right" style="padding:10px 16px;font-family:Arial,sans-serif;font-size:12px;color:#5e6d85;text-transform:uppercase;letter-spacing:.5px;">Baj&oacute;</th>
        </tr>{''.join(filas)}
      </table>{mas}
      <p style="margin:16px 0 0;font-family:Arial,sans-serif;font-size:12px;color:#8a97ab;">Compara siempre en las fuentes oficiales antes de comprar: los precios de vuelos son din&aacute;micos.</p>
      <!-- BOTÓN bulletproof -->
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" style="margin:22px auto 26px;">
        <tr><td bgcolor="#{NARANJA}" style="background:#{NARANJA};border-radius:9px;">
          <a href="{SITE}" style="display:inline-block;padding:14px 30px;font-family:Arial,sans-serif;font-size:16px;font-weight:bold;color:#ffffff;text-decoration:none;border-radius:9px;">Ver el panel completo</a>
        </td></tr>
      </table>
    </td></tr>

    <!-- FOOTER -->
    <tr><td bgcolor="#073b9e" style="background:#073b9e;border-radius:0 0 14px 14px;padding:20px 28px;">
      <p style="margin:0 0 8px;font-family:Arial,sans-serif;font-size:12px;color:#bbd3f5;line-height:1.5;">
        Para desuscribirte: en el panel, secci&oacute;n <b style="color:#ffffff;">Alertas</b>, bot&oacute;n &laquo;Darme de baja&raquo; (mismo correo con el que te suscribiste).</p>
      <p style="margin:0;font-family:Arial,sans-serif;font-size:11px;color:#8fb0e8;line-height:1.5;">
        Tu correo vive cifrado (RSA-2048) y en el listado p&uacute;blico solo se ve la primera y la &uacute;ltima letra (ej: j&bull;&bull;&bull;m).</p>
    </td></tr>

  </table>
</td></tr>
</table>
</body>
</html>"""


def enviar(user, pwd, dest, alertas, port=587, host="") -> None:
    if port == 465:
        srv = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        srv = smtplib.SMTP(host, port, timeout=30)
    with srv as s:
        if port != 465:
            s.starttls()
        s.login(user, pwd)
        msg = MIMEMultipart("alternative")
        msg["Subject"] = Header(
            f"[Radar Río] Bajó {clp(alertas[0]['baja'])} en {alertas[0]['serie']}", "utf-8")
        msg["From"] = user
        msg["To"] = dest
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid(domain="vuelos-rio-live")
        msg.attach(MIMEText(email_text(alertas), "plain", "utf-8"))
        msg.attach(MIMEText(email_html(alertas), "html", "utf-8"))
        s.sendmail(user, [dest], msg.as_string())


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
        print(f"Hay bajadas pero NADIE suscrito todavía: marcadas como notificadas.")
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
    orden = sorted(nuevos, key=lambda a: a["baja"], reverse=True)
    ok = 0
    for dest in subs:
        try:
            enviar(user, pwd, dest, orden, port, host)
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
