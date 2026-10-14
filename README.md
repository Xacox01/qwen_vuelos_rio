# ✈️ Radar Río — Dashboard en vivo SCL ⇄ Río de Janeiro + playas LatAm

Dashboard público en **GitHub Pages**: `https://Xacox01.github.io/vuelos-rio-live/`
Verano 2027 (ene–feb) · viaje ≥ 9 noches · regreso ≤ 23-feb · grupo de hasta 9 pasajeros.

## Qué hace
- **Cada ~5 min** (GitHub Actions) consulta en vivo:
  - **APIs oficiales**: JetSMART (availability), SKY (Sputnik), LATAM (BFF calendario)
  - **Cocha** (cotización real por API GraphQL) y **Skiplagged** (metasearch global)
  - **Playas LatAm**: 16 destinos bajo $500.000 pp (Skiplagged + Cocha)
  - **Falabella Viajes / Despegar**: navegador stealth Camoufox (bypass DataDome) cada ~30 min + fallback
- Detecta **bajadas de precio ≥ 0,5%** y las registra (alertas + correo opcional).
- Publica `data.json` solo cuando hay cambios; la web se auto-refresca cada 60 s.

## Estructura
| Archivo | Rol |
|---|---|
| `index.html` | Dashboard completo (Vue 3 por CDN, sin build) |
| `scraper.py` | Orquestador: todas las fuentes + alertas + `data.json` |
| `fetch_otas.py` | Módulo stealth (Camoufox) para Falabella/Despegar |
| `scan_otas_local.py` | Escáner OTA para correr en TU PC (ver abajo) |
| `probe_waf.py` / `diag_otas.py` | Diagnóstico del WAF DataDome |
| `.github/workflows/refresh.yml` | Ciclo cada ~5 min con auto-encadenado |
| `verify.js` | Arnés de regresión (207 checks) — `node verify.js index.html data.json` |

## 🏠 Falabella/Despegar frescos desde tu PC (recomendado)
DataDome (su WAF) es agresivo con IPs de datacenter (Actions/Azure) y a veces exige
captcha interactivo que ningún navegador headless puede auto-solver. Desde una IP
residencial (tu casa) pasa casi siempre:

```bash
pip install camoufox && python -m camoufox fetch
python scan_otas_local.py --push    # ~4 min; actualiza y publica
```

En Actions el bypass es **oportunista**: el ciclo intenta cada ~30 min con
`humanize` y bajo volumen (4 páginas); cuando DataDome deja pasar la IP del runner,
los precios se publican solos y quedan etiquetados como verificados. Si no,
la web conserva el último dato real con su marca de frescura (los datos viejos
se excluyen del ranking automáticamente).

## Administración
- Refresco manual: repo → **Actions** → *refresh* → **Run workflow**.
- Cambiar cadencia del escaneo OTA: variable de entorno `OTAS_GAP` (segundos, default 1800).
- Ver historial: Actions → refresh (cada corrida deja el detalle de fuentes).
- Costo: $0 (repo público).

## Notas técnicas del bypass (metodología estilo [rea](https://github.com/morluto/rea))
1. Mapeo del perímetro: DataDome protege `/`, `/vuelos*`, `/flights*` y `/shop/flights/*`;
   el resto de rutas llega al origen (verificado por sondas de estado).
2. `api.despegar.com` expone portal Swagger sin WAF, pero el endpoint histórico
   `/v3/flights/searches` ya no existe; la búsqueda web vive tras el WAF.
3. Camoufox (Firefox stealth) pasa el challenge JS de DataDome desde IPs limpias
   (verificado: HTTP 200 con SPA completa en Actions). Los runners repetitivos
   terminan baneados → estrategia de escaneo espaciado + bajo volumen + caché.
