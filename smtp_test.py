#!/usr/bin/env python3
"""Diagnóstico SMTP: envía UN correo de prueba a SMTP_USER (tu propia bandeja).

Se corre manualmente desde GitHub Actions: workflow "diagnostico-smtp" → Run workflow.
Verifica, en orden: configuración → conexión → TLS → login → entrega.
Nunca imprime secretos: los correos siempre salen enmascarados (j•••m).

Si el correo llega (revisa también Spam), el envío de alertas funcionará igual,
porque send_alerts.py usa la misma cuenta, puerto y autenticación.
"""
import os
import smtplib
import sys
from datetime import datetime, timezone
from email.header import Header
from email.mime.text import MIMEText


def mask(e: str) -> str:
    """Enmascara un correo: solo letra inicial y final de la parte local."""
    e = (e or "").strip()
    if "@" not in e:
        return "•••"
    loc, dom = e.split("@", 1)
    if len(loc) >= 2:
        return f"{loc[0]}{'•' * max(len(loc) - 2, 3)}{loc[-1]}@{dom}"
    return f"{loc}@{dom}"


def fail(step: str, detail: str, hint: str = "") -> None:
    print(f"\n✗ FALLO en {step}: {detail}")
    if hint:
        print(f"  → Qué revisar: {hint}")
    print("\nRESULTADO: CONFIGURACIÓN INCOMPLETA (no se pudo enviar)")
    sys.exit(1)


def ok(step: str, detail: str = "") -> None:
    print(f"✓ {step}" + (f" — {detail}" if detail else ""))


def main() -> None:
    host = (os.environ.get("SMTP_HOST") or "").strip()
    user = (os.environ.get("SMTP_USER") or "").strip()
    raw_pwd = os.environ.get("SMTP_PASS") or ""
    pwd = raw_pwd.replace(" ", "").replace("\u00a0", "").strip()
    port = int(os.environ.get("SMTP_PORT") or "587")

    print("== Diagnóstico SMTP de vuelos-rio-live ==")
    print(f"Host: {host or '(vacío)'} | Puerto: {port} | Cuenta: {mask(user)}")

    # 1. Configuración
    if not host or "." not in host:
        fail("configuración", f"SMTP_HOST = '{host or '(vacío)'}'",
             "para Gmail el valor debe ser exactamente: smtp.gmail.com")
    if "@" not in user:
        fail("configuración", "SMTP_USER no parece un correo",
             "usa tu dirección completa, ej: tunombre@gmail.com")
    if not pwd:
        fail("configuración", "SMTP_PASS está vacío",
             "agrega el secreto SMTP_PASS con la contraseña de aplicación")
    if len(pwd) != 16:
        print(f"  (aviso) SMTP_PASS tiene {len(pwd)} caracteres; "
              f"la de Gmail son 16. Igual intento el login…")
    if pwd != raw_pwd.strip():
        ok("limpieza", "SMTP_PASS tenía espacios y fueron eliminados "
                       "automáticamente (Gmail la muestra en bloques de 4)")
    ok("configuración")

    # 2. Conexión + TLS + login
    try:
        if port == 465:
            srv = smtplib.SMTP_SSL(host, port, timeout=30)
            tls_mode = "SSL implícito"
        else:
            srv = smtplib.SMTP(host, port, timeout=30)
            srv.starttls()
            tls_mode = "STARTTLS"
        with srv as s:
            ok("conexión", f"{host}:{port} con {tls_mode}")
            code, resp = s.login(user, pwd)
            ok("login", f"Gmail aceptó las credenciales ({code})")

            # 3. Envío del correo de prueba a la propia cuenta
            now = datetime.now(timezone.utc).strftime("%d-%m-%Y %H:%M UTC")
            body = (
                "Hola,\n\n"
                "Este es un correo de PRUEBA del sistema de notificaciones de\n"
                "https://xacox01.github.io/vuelos-rio-live/\n\n"
                "Si estás leyendo esto, la configuración de envío quedó funcionando:\n"
                f"  • Servidor: {host}:{port} ({tls_mode})\n"
                f"  • Cuenta de envío: {mask(user)}\n"
                f"  • Fecha: {now}\n\n"
                "Desde ahora, cuando el panel detecte una baja de precio en los\n"
                "vuelos Santiago ⇄ Río de Janeiro (ene–feb 2027), los suscriptores\n"
                "recibirán un aviso como este. Los correos de los suscriptores\n"
                "viven cifrados y en el listado público solo se ve la primera y\n"
                "la última letra (ej: j•••m).\n\n"
                "— El robot de vuelos-rio-live"
            )
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = Header(
                "[vuelos-rio-live] Correo de prueba — configuración correcta", "utf-8")
            msg["From"] = user
            msg["To"] = user
            refuse = s.sendmail(user, [user], msg.as_string())
            ok("envío", "Gmail aceptó el mensaje para entrega"
               + (f" (rechazos parciales: {refuse})" if refuse else ""))
    except smtplib.SMTPAuthenticationError as e:
        fail("login", f"Gmail rechazó las credenciales ({e.smtp_code})",
             "confirma que SMTP_PASS sea la CONTRASEÑA DE APLICACIÓN de 16 "
             "caracteres (https://myaccount.google.com/apppasswords), NO tu "
             "contraseña normal de Gmail; y que SMTP_USER sea el mismo correo "
             "que generó esa contraseña")
    except smtplib.SMTPConnectError as e:
        fail("conexión", str(e)[:120], f"¿{host}:{port} es correcto? Para Gmail: "
             "smtp.gmail.com puerto 587 (o 465)")
    except (smtplib.SMTPException, OSError) as e:
        fail("conexión/envío", f"{type(e).__name__}: {str(e)[:120]}",
             "reintenta con Run workflow; si persiste, revisa el nombre del host")

    print("\nRESULTADO: OK ✅ — correo de prueba enviado a " + mask(user))
    print("Revisa la bandeja de entrada (y la carpeta Spam) de esa cuenta.")


if __name__ == "__main__":
    main()
