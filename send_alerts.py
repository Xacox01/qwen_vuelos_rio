#!/usr/bin/env python3
"""Alertas de bajada de precio por correo (HTML con identidad Radar Río).
Corre tras notificaciones.py en el workflow, antes de publicar.

VENTANA DE ENVÍO (pedido del dueño): los correos SOLO salen entre 09:00 y
20:00 hora de Santiago (America/Santiago). Fuera de esa ventana no se envía
nada y las alertas se acumulan; el PRIMER envío de la mañana es un RESUMEN
DE LA NOCHE: movimientos de precio entre el cierre de la ventana anterior
(~20:00, se congela una copia de los precios en data.json: "noche") y la
hora del envío — incluye también SUBIDAS, las bajadas que quedaron
pendientes de la tarde anterior y los mejores precios vigentes. Durante el
día las alertas de bajada siguen saliendo en vivo, igual que antes.

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
import re
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

# Hora oficial de los envíos: Santiago. Los ts de data.json/alertas los escribe
# el runner de Actions con reloj UTC ("%Y-%m-%d %H:%M:%S") y se convierten.
try:
    from zoneinfo import ZoneInfo
    STGO = ZoneInfo("America/Santiago")
except Exception:  # sin tzdata: Chile continental en verano = UTC-3 (en Actions siempre hay zoneinfo)
    STGO = timezone(timedelta(hours=-3))

H_INICIO = 9    # primera hora con envíos (09:00 Santiago)
H_FIN = 20      # primera hora SIN envíos (20:00 Santiago)
UMBRAL_DIF = 1000    # CLP mínimos para contar un movimiento en el resumen
UMBRAL_PCT = 0.005   # 0,5 % (mismo criterio anti-ruido que las alertas)

DATA = "data.json"
SITE = "https://xacox01.github.io/vuelos-rio-live/"
LOGO_BLANCO = SITE + "assets/brand/logo-blanco-512.png"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notificaciones import cargar_emails, mask  # noqa: E402

NARANJA = "ff690f"  # color oficial del botón (bgcolor sin # para clientes antiguos)

# ---- Nombres de ciudad (pedido del dueño: en el correo, cero códigos de aeropuerto) ----
CIUDADES = {
    "SCL": "Santiago", "GIG": "Río de Janeiro", "RIO": "Río de Janeiro",
    "GRU": "São Paulo", "CGH": "São Paulo", "EZE": "Buenos Aires", "AEP": "Buenos Aires",
    "LIM": "Lima", "BOG": "Bogotá", "MDE": "Medellín", "CTG": "Cartagena", "ADZ": "San Andrés",
    "CUN": "Cancún", "PUJ": "Punta Cana", "SDQ": "Santo Domingo", "HAV": "La Habana",
    "MEX": "Ciudad de México", "PTY": "Panamá", "SJO": "San José", "LIR": "Liberia",
    "MVD": "Montevideo", "ASU": "Asunción", "VVI": "Santa Cruz", "LPB": "La Paz",
    "GYE": "Guayaquil", "UIO": "Quito", "MIA": "Miami", "JFK": "Nueva York",
    "LAX": "Los Ángeles", "MAD": "Madrid", "BCN": "Barcelona", "FLN": "Florianópolis",
    "SSA": "Salvador de Bahía", "REC": "Recife", "FOR": "Fortaleza", "MCZ": "Maceió",
    "NAT": "Natal", "BPS": "Porto Seguro", "NVD": "Navegantes", "IGU": "Foz de Iguazú",
}
FUENTES_ALERTA = {"sky", "jetsmart", "latam", "cocha", "despegar", "falabella",
                  "skiplagged", "playas", "google"}

def ruta_bonita(serie) -> str:
    """Convierte las series de alerta a nombres de ciudad, sin códigos IATA:
    'SKY SCL-Río'   → 'SKY · Santiago → Río de Janeiro'
    'Playas GIG'    → 'Playas · Río de Janeiro'
    'Cocha SCL-GIG' → 'Cocha · Santiago → Río de Janeiro'
    La plataforma crecerá a toda América: cualquier código nuevo que no esté en el
    mapa se muestra tal cual (nunca se inventa una ciudad)."""
    s = str(serie).strip()
    partes = s.split(" ", 1)
    fuente = ""
    if len(partes) == 2 and partes[0].lower() in FUENTES_ALERTA:
        fuente, ruta = partes
    else:
        ruta = s
    ruta = re.sub(r"\b[A-Z]{3}\b", lambda m: CIUDADES.get(m.group(0), m.group(0)), ruta)
    ruta = re.sub(r"\bRío\b", "Río de Janeiro", ruta)
    ruta = ruta.replace("Río de Janeiro de Janeiro", "Río de Janeiro")
    if "-" in ruta:
        a, b = ruta.split("-", 1)
        ruta = f"{a.strip()} → {b.strip()}"
    return f"{fuente} · {ruta}" if fuente else ruta


def clp(n) -> str:
    return "${:,.0f}".format(n).replace(",", ".")


# ---------------- Ventana horaria y resumen de la noche ----------------

def ahora_stgo() -> datetime:
    return datetime.now(tz=STGO)


def dentro_ventana(now=None) -> bool:
    """True entre 09:00 (incluida) y 20:00 (excluida), hora de Santiago."""
    now = now or ahora_stgo()
    return H_INICIO <= now.hour < H_FIN


def ancla_noche(now: datetime) -> str:
    """Fecha (Santiago) de la tarde que inició la noche en curso:
    21:00 del 16 y 03:00 del 17 pertenecen a la MISMA noche → '2026-10-16'."""
    ref = now if now.hour >= H_FIN else now - timedelta(days=1)
    return ref.date().isoformat()


def guardar_noche(d, now: datetime) -> bool:
    """Congela UNA vez por noche (primer ciclo con hora >= 20:00) el mapa de
    precios actual en data.json: "noche". Con esa referencia el resumen de la
    mañana puede reportar también SUBIDAS, no solo las bajadas del motor."""
    ancla = ancla_noche(now)
    if (d.get("noche") or {}).get("fecha") == ancla:
        return False
    d["noche"] = {"fecha": ancla, "ts_stgo": now.strftime("%H:%M"),
                  "minimos": dict(d.get("minimos") or {})}
    return True


def hora_stgo(ts) -> str:
    """'2026-10-16 22:05:30' (UTC del runner) → '19:05' hora de Santiago."""
    try:
        t = datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return t.astimezone(STGO).strftime("%H:%M")
    except Exception:
        s = str(ts)
        return s[11:16] if len(s) >= 16 else "—"


def fecha_stgo(ts):
    """Fecha calendario (Santiago) de un ts UTC del runner; None si es inválido."""
    try:
        t = datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return t.astimezone(STGO).date()
    except Exception:
        return None


def movimientos_nocturnos(d):
    """Compara los precios congelados al cierre de la noche contra los actuales.
    Devuelve (bajas, subidas, ref) ordenadas por magnitud; mismo umbral
    anti-ruido que las alertas (>= $1.000 y >= 0,5 %)."""
    ref = d.get("noche") or {}
    actual = d.get("minimos") or {}
    bajas, subidas = [], []
    for serie, antes in (ref.get("minimos") or {}).items():
        ahora = actual.get(serie)
        if not antes or not ahora or ahora == antes:
            continue
        dif = antes - ahora
        if abs(dif) < UMBRAL_DIF or abs(dif) / antes < UMBRAL_PCT:
            continue
        item = {"serie": serie, "antes": antes, "ahora": ahora,
                "dif": abs(dif), "pct": round(abs(dif) / antes * 100, 1)}
        (bajas if dif > 0 else subidas).append(item)
    bajas.sort(key=lambda x: x["dif"], reverse=True)
    subidas.sort(key=lambda x: x["dif"], reverse=True)
    return bajas, subidas, ref


def mejor_ruta(d):
    """Mejor precio vigente de la ruta principal (series '*SCL-Río')."""
    vals = [(p, s) for s, p in (d.get("minimos") or {}).items()
            if p and str(s).endswith("SCL-Río")]
    return min(vals) if vals else None


def mejores_ahora(d, n=5):
    vals = [(p, s) for s, p in (d.get("minimos") or {}).items() if p]
    return sorted(vals)[:n]


def pendientes_de_envio(d):
    last = d.get("alerts_sent_ts") or ""
    return sorted((a for a in (d.get("alerts") or []) if a["ts"] > last),
                  key=lambda a: a["ts"])


def email_text(alertas) -> str:
    lines = ["Bajada de precios detectada en vuelos Santiago ⇄ Río de Janeiro (ene–feb 2027):", ""]
    for a in alertas:
        lines.append(f"• {ruta_bonita(a['serie'])}: {clp(a['antes'])} → {clp(a['ahora'])} "
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
    preheader = f"{ruta_bonita(first['serie'])}: {clp(first['antes'])} → {clp(first['ahora'])} (−{first['pct']}%)"

    filas = []
    for a in alertas[:8]:
        filas.append(f"""
      <tr>
        <td style="padding:12px 16px;border-bottom:1px solid #e3e7ec;font-family:Arial,sans-serif;font-size:14px;color:#1a2b49;font-weight:bold;">{ruta_bonita(a['serie'])}</td>
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


# ---------------- Resumen de la noche (primer envío del día) ----------------

def digest_subject(d) -> str:
    bajas, subidas, _ = movimientos_nocturnos(d)
    mejor = mejor_ruta(d)
    cola = f"mejor precio {clp(mejor[0])}" if mejor else "mejores precios en el panel"
    if bajas:
        return f"[Radar Río] Resumen de la noche: {len(bajas)} bajada(s) · {cola}"
    if subidas:
        return f"[Radar Río] Resumen de la noche: sin bajadas ({len(subidas)} subida/s) · {cola}"
    return f"[Radar Río] Resumen de la noche: precios estables · {cola}"


def digest_text(d) -> str:
    now = ahora_stgo()
    bajas, subidas, ref = movimientos_nocturnos(d)
    pend = pendientes_de_envio(d)
    lines = ["Resumen de la noche — vuelos Santiago ⇄ Río de Janeiro (ene–feb 2027):", ""]
    if ref.get("minimos"):
        lines.append(f"Movimientos entre {ref.get('ts_stgo', '20:00')} y {now.strftime('%H:%M')} "
                     f"(hora de Santiago): {len(bajas)} bajada(s), {len(subidas)} subida(s).")
        for a in bajas[:8]:
            lines.append(f"• BAJÓ {ruta_bonita(a['serie'])}: {clp(a['antes'])} → {clp(a['ahora'])} (−{a['pct']}%)")
        for a in subidas[:4]:
            lines.append(f"• SUBIÓ {ruta_bonita(a['serie'])}: {clp(a['antes'])} → {clp(a['ahora'])} (+{a['pct']}%)")
    else:
        lines.append("Sin referencia de la noche: los precios se congelan cada día a las "
                     "20:00 (hora de Santiago); mañana habrá resumen comparado.")
    if pend:
        lines += ["", "Bajadas de la tarde de ayer que quedaron pendientes de envío:"]
        for a in pend[-8:]:
            lines.append(f"• {hora_stgo(a['ts'])} · {ruta_bonita(a['serie'])}: "
                         f"{clp(a['antes'])} → {clp(a['ahora'])} (−{a['pct']}%)")
    mejor = mejores_ahora(d)
    if mejor:
        lines += ["", "Mejores precios ahora:"]
        for p, s in mejor:
            lines.append(f"• {ruta_bonita(s)}: {clp(p)}")
    lines += ["", f"Ver el panel actualizado: {SITE}", "",
              "Este es el primer correo del día; los envíos salen entre 09:00 y 20:00 hora "
              "de Santiago y las alertas de bajada llegan en vivo dentro de esa ventana.", "",
              "Para desuscribirte: en el panel, sección Alertas, botón «Darme de baja» "
              "(mismo correo con el que te suscribiste).",
              "Tu correo está guardado cifrado y nunca se muestra en el listado público."]
    return "\n".join(lines)


def _kpi(valor, etiqueta, color) -> str:
    return (f'<td width="33%" align="center" bgcolor="#eef3fe" '
            f'style="padding:14px 6px;border-left:2px solid #ffffff;">'
            f'<div style="font-family:Arial,sans-serif;font-size:21px;font-weight:bold;color:{color};">{valor}</div>'
            f'<div style="font-family:Arial,sans-serif;font-size:10px;color:#5e6d85;'
            f'letter-spacing:1px;text-transform:uppercase;">{etiqueta}</div></td>')


def _fila_mov(a, subio=False) -> str:
    """Fila de movimiento nocturno: baja en verde, subida en rojo."""
    bg, tx = ("#fdecea", "#b3261e") if subio else ("#e8f7ee", "#0a7d43")
    signo = "+" if subio else "&minus;"
    return f"""
      <tr>
        <td style="padding:11px 16px;border-bottom:1px solid #e3e7ec;font-family:Arial,sans-serif;font-size:14px;color:#1a2b49;font-weight:bold;">{ruta_bonita(a['serie'])}</td>
        <td style="padding:11px 8px;border-bottom:1px solid #e3e7ec;font-family:Arial,sans-serif;font-size:14px;color:#8a97ab;text-align:right;white-space:nowrap;">{clp(a['antes'])}</td>
        <td style="padding:11px 8px;border-bottom:1px solid #e3e7ec;font-family:Arial,sans-serif;font-size:15px;color:#073b9e;font-weight:bold;text-align:right;white-space:nowrap;">{clp(a['ahora'])}</td>
        <td style="padding:11px 16px;border-bottom:1px solid #e3e7ec;text-align:right;">
          <span style="background:{bg};color:{tx};font-family:Arial,sans-serif;font-size:12px;font-weight:bold;padding:4px 10px;border-radius:999px;white-space:nowrap;">{signo}{a['pct']}%</span>
        </td>
      </tr>"""


def _fila_pend(a) -> str:
    """Fila de bajada pendiente de la tarde anterior (con hora local)."""
    return f"""
      <tr>
        <td style="padding:11px 12px;border-bottom:1px solid #e3e7ec;font-family:Arial,sans-serif;font-size:13px;color:#5e6d85;white-space:nowrap;">{hora_stgo(a['ts'])}</td>
        <td style="padding:11px 8px;border-bottom:1px solid #e3e7ec;font-family:Arial,sans-serif;font-size:14px;color:#1a2b49;font-weight:bold;">{ruta_bonita(a['serie'])}</td>
        <td style="padding:11px 8px;border-bottom:1px solid #e3e7ec;font-family:Arial,sans-serif;font-size:14px;color:#8a97ab;text-decoration:line-through;text-align:right;white-space:nowrap;">{clp(a['antes'])}</td>
        <td style="padding:11px 16px;border-bottom:1px solid #e3e7ec;text-align:right;">
          <span style="background:#fff1e5;color:#c24f00;font-family:Arial,sans-serif;font-size:12px;font-weight:bold;padding:4px 10px;border-radius:999px;white-space:nowrap;">&minus;{a['pct']}%</span>
        </td>
      </tr>"""


def digest_html(d) -> str:
    now = ahora_stgo()
    bajas, subidas, ref = movimientos_nocturnos(d)
    pend = pendientes_de_envio(d)
    ruta_top = mejor_ruta(d)
    preheader = (f"{len(bajas)} bajada(s) y {len(subidas)} subida(s) en la noche; "
                 f"mejor precio {clp(ruta_top[0])} en {ruta_bonita(ruta_top[1])}") if ruta_top \
        else "Resumen de precios de la noche en vuelos Santiago ⇄ Río de Janeiro"

    secciones = ""
    if ref.get("minimos"):
        filas = "".join(_fila_mov(a) for a in bajas[:8]) + \
                "".join(_fila_mov(a, True) for a in subidas[:4])
        if filas:
            restantes = max(0, len(bajas) - 8) + max(0, len(subidas) - 4)
            nota = (f"<p style=\"margin:10px 0 0;font-family:Arial,sans-serif;font-size:12px;color:#5e6d85;\">"
                    f"Movimientos entre {ref.get('ts_stgo', '20:00')} y {now.strftime('%H:%M')} hora de Santiago"
                    + (f" · hay {restantes} movimiento(s) más en el panel.</p>" if restantes else ".</p>"))
            secciones += f"""
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid #e3e7ec;border-radius:10px;">
        <tr style="background:#eef3fe;">
          <th align="left" style="padding:10px 16px;font-family:Arial,sans-serif;font-size:12px;color:#5e6d85;text-transform:uppercase;letter-spacing:.5px;">Serie</th>
          <th align="right" style="padding:10px 8px;font-family:Arial,sans-serif;font-size:12px;color:#5e6d85;text-transform:uppercase;letter-spacing:.5px;">Cierre</th>
          <th align="right" style="padding:10px 8px;font-family:Arial,sans-serif;font-size:12px;color:#5e6d85;text-transform:uppercase;letter-spacing:.5px;">Ahora</th>
          <th align="right" style="padding:10px 16px;font-family:Arial,sans-serif;font-size:12px;color:#5e6d85;text-transform:uppercase;letter-spacing:.5px;">Dif.</th>
        </tr>{filas}
      </table>{nota}"""
        else:
            secciones += """
      <p style="margin:0 0 4px;font-family:Arial,sans-serif;font-size:15px;color:#0a7d43;font-weight:bold;">Noche tranquila</p>
      <p style="margin:0;font-family:Arial,sans-serif;font-size:14px;color:#5e6d85;">Sin movimientos de precio relevantes durante la noche (umbral: $1.000 o 0,5&nbsp;%).</p>"""
    else:
        secciones += """
      <p style="margin:0;font-family:Arial,sans-serif;font-size:14px;color:#5e6d85;">Aún no hay referencia de la noche: el sistema congela los precios cada día a las 20:00 (hora de Santiago) para compararlos cada mañana.</p>"""

    if pend:
        f_pend = "".join(_fila_pend(a) for a in pend[-8:])
        mas_p = (f"<p style=\"margin:10px 0 0;font-family:Arial,sans-serif;font-size:12px;color:#5e6d85;\">"
                 f"Y {len(pend) - 8} bajada(s) más en el panel…</p>") if len(pend) > 8 else ""
        secciones += f"""
      <h2 style="margin:22px 0 10px;font-family:Arial,sans-serif;font-size:16px;color:#1a2b49;">Bajadas de ayer tarde (pendientes)</h2>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid #e3e7ec;border-radius:10px;">
        <tr style="background:#eef3fe;">
          <th align="left" style="padding:10px 12px;font-family:Arial,sans-serif;font-size:12px;color:#5e6d85;text-transform:uppercase;letter-spacing:.5px;">Hora</th>
          <th align="left" style="padding:10px 8px;font-family:Arial,sans-serif;font-size:12px;color:#5e6d85;text-transform:uppercase;letter-spacing:.5px;">Ruta</th>
          <th align="right" style="padding:10px 8px;font-family:Arial,sans-serif;font-size:12px;color:#5e6d85;text-transform:uppercase;letter-spacing:.5px;">Antes</th>
          <th align="right" style="padding:10px 16px;font-family:Arial,sans-serif;font-size:12px;color:#5e6d85;text-transform:uppercase;letter-spacing:.5px;">Ahora</th>
        </tr>{f_pend}
      </table>{mas_p}"""

    top5 = mejores_ahora(d)
    if top5:
        lis = "".join(
            f"<tr><td style=\"padding:7px 0;border-bottom:1px solid #eef1f5;font-family:Arial,sans-serif;font-size:13px;color:#1a2b49;\">{ruta_bonita(s)}</td>"
            f"<td style=\"padding:7px 0;border-bottom:1px solid #eef1f5;font-family:Arial,sans-serif;font-size:13px;color:#073b9e;font-weight:bold;text-align:right;\">{clp(p)}</td></tr>"
            for p, s in top5)
        secciones += f"""
      <h2 style="margin:22px 0 10px;font-family:Arial,sans-serif;font-size:16px;color:#1a2b49;">Mejores precios ahora</h2>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{lis}</table>"""

    kpis = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin:0 0 18px;border-radius:10px;">'
            f"<tr>{_kpi(len(bajas), 'bajadas', '#0a7d43')}"
            f"{_kpi(len(subidas), 'subidas', '#b3261e')}"
            f"{_kpi(clp(ruta_top[0]) if ruta_top else '&mdash;', 'mejor SCL&ndash;R&iacute;o', '#0a4fd9')}</tr></table>")

    hoy = now.strftime("%d-%m-%Y %H:%M") + " Santiago"
    return f"""<!DOCTYPE html>
<html lang="es" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>Resumen de la noche · Radar Río</title>
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
      <h1 style="margin:0 0 6px;font-family:Arial,sans-serif;font-size:22px;color:#1a2b49;">Resumen de la noche</h1>
      <p style="margin:0 0 18px;font-family:Arial,sans-serif;font-size:14px;color:#5e6d85;">Vuelos Santiago &hArr; R&iacute;o de Janeiro (ene&ndash;feb 2027) &middot; primer correo del d&iacute;a &middot; {hoy}</p>
      {kpis}
      {secciones}
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
        Env&iacute;os solo entre 09:00 y 20:00 (hora de Santiago). Para desuscribirte: en el panel, secci&oacute;n <b style="color:#ffffff;">Alertas</b>, bot&oacute;n &laquo;Darme de baja&raquo; (mismo correo con el que te suscribiste).</p>
      <p style="margin:0;font-family:Arial,sans-serif;font-size:11px;color:#8fb0e8;line-height:1.5;">
        Tu correo vive cifrado (RSA-2048) y en el listado p&uacute;blico solo se ve la primera y la &uacute;ltima letra (ej: j&bull;&bull;&bull;m).</p>
    </td></tr>

  </table>
</td></tr>
</table>
</body>
</html>"""


def _conexion(port, host):
    if port == 465:
        return smtplib.SMTP_SSL(host, port, timeout=30)
    return smtplib.SMTP(host, port, timeout=30)


def _enviar_mime(user, pwd, dest, subject, texto, html, port=587, host="") -> None:
    with _conexion(port, host) as s:
        if port != 465:
            s.starttls()
        s.login(user, pwd)
        msg = MIMEMultipart("alternative")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = user
        msg["To"] = dest
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid(domain="vuelos-rio-live")
        msg.attach(MIMEText(texto, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        s.sendmail(user, [dest], msg.as_string())


def enviar(user, pwd, dest, alertas, port=587, host="") -> None:
    first = alertas[0]
    subject = f"[Radar Río] Bajó {clp(first['baja'])} en {ruta_bonita(first['serie'])}"
    _enviar_mime(user, pwd, dest, subject, email_text(alertas), email_html(alertas), port, host)


def enviar_resumen(user, pwd, dest, d, port=587, host="") -> None:
    """Correo del primer envío del día: resumen de lo que pasó en la noche."""
    _enviar_mime(user, pwd, dest, digest_subject(d), digest_text(d), digest_html(d), port, host)


def smtp_creds():
    host = (os.environ.get("SMTP_HOST") or "").strip()
    user = (os.environ.get("SMTP_USER") or "").strip()
    # Gmail muestra la contraseña de aplicación en bloques de 4 separados por
    # espacios: se eliminan automáticamente por si se copió tal cual.
    pwd = (os.environ.get("SMTP_PASS") or "").replace(" ", "").replace("\u00a0", "").strip()
    port = int(os.environ.get("SMTP_PORT") or "587")
    return host, user, pwd, port


def _guardar(d) -> None:
    json.dump(d, open(DATA, "w"), ensure_ascii=False)


def _resumen_del_dia(d, subs, nuevos, now) -> None:
    """Primer envío del día: RESUMEN DE LA NOCHE (pedido del dueño). Sale
    siempre que haya suscriptores, haya o no movimientos: si la noche fue
    tranquila, lo dice. Además consume las alertas pendientes de la tarde
    anterior para que nunca se envíen sueltas más tarde. La marca
    "resumen_enviado" (fecha Santiago) evita duplicados: alerts_sent_ts es el
    watermark de ALERTAS y puede quedar en la tarde del día anterior."""
    hoy = now.date().isoformat()
    if not subs:
        if nuevos:
            d["alerts_sent_ts"] = max(a["ts"] for a in nuevos)
        d["email_pending"] = False
        d["resumen_enviado"] = hoy
        _guardar(d)
        print("Resumen de la mañana: NADIE suscrito; pendientes marcadas como notificadas.")
        return
    host, user, pwd, port = smtp_creds()
    if not (host and user and pwd):
        print(f"SMTP sin configurar: el resumen de la mañana queda pendiente para "
              f"{len(subs)} suscrito(s) ({', '.join(mask(s) for s in subs[:5])}).")
        return
    ok = 0
    for dest in subs:
        try:
            enviar_resumen(user, pwd, dest, d, port, host)
            ok += 1
            print(f"resumen de la noche enviado → {mask(dest)}")
        except Exception as e:
            print(f"FALLO envío resumen a {mask(dest)}: {str(e)[:80]}")
    if ok:
        if nuevos:
            d["alerts_sent_ts"] = max(a["ts"] for a in nuevos)
        d["email_pending"] = False
        d["resumen_enviado"] = hoy
        _guardar(d)
        print(f"OK: {ok}/{len(subs)} resúmenes de la noche enviados.")
    else:
        print("Ningún resumen exitoso: se reintentará el próximo ciclo.")


def main():
    d = json.load(open(DATA, encoding="utf8"))
    now = ahora_stgo()
    # ---- Fuera de la ventana 09:00–20:00 (Santiago): no se envía nada ----
    if not dentro_ventana(now):
        congelado = guardar_noche(d, now)
        if congelado:
            _guardar(d)
        print(f"Fuera de la ventana de envío ({now.strftime('%H:%M')} Santiago): correos "
              f"solo entre 09:00 y 20:00. "
              + ("Precios de la noche congelados para el resumen de la mañana."
                 if congelado else "Referencia nocturna ya registrada."))
        return
    nuevos = pendientes_de_envio(d)
    # ---- Primer envío del día: RESUMEN DE LA NOCHE (marca anti-duplicado) ----
    if d.get("resumen_enviado") != now.date().isoformat():
        _resumen_del_dia(d, cargar_emails(), nuevos, now)
        return
    # ---- Resto del día: alertas de bajada en vivo (comportamiento clásico) ----
    if not d.get("email_pending"):
        print("Sin alertas pendientes."); return
    if not nuevos:
        d["email_pending"] = False
        _guardar(d)
        print("email_pending sin alertas nuevas: limpio.")
        return
    subs = cargar_emails()
    max_ts = max(a["ts"] for a in nuevos)
    if not subs:
        d["alerts_sent_ts"] = max_ts; d["email_pending"] = False
        _guardar(d)
        print("Hay bajadas pero NADIE suscrito todavía: marcadas como notificadas.")
        return
    host, user, pwd, port = smtp_creds()
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
        _guardar(d)
        print(f"OK: {ok}/{len(subs)} correos enviados.")
    else:
        print("Ningún envío exitoso: se reintentará el próximo ciclo.")


if __name__ == "__main__":
    main()
