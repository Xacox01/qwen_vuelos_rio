# ✈️ Radar Río — Dashboard en vivo SCL ⇄ Río de Janeiro + playas LatAm

Dashboard público en **GitHub Pages**: `https://Xacox01.github.io/vuelos-rio-live/`
Verano 2027 (ene–feb) · viaje ≥ 9 noches · regreso ≤ 23-feb · grupo de hasta 9 pasajeros.

## Qué hace
- **Cada ~5 min** (GitHub Actions) consulta en vivo:
  - **APIs oficiales**: JetSMART (availability), SKY (Sputnik), LATAM (BFF calendario)
  - **Cocha** (cotización real por API GraphQL) y **Skiplagged** (metasearch global)
  - **Playas LatAm**: 16 destinos bajo $500.000 pp (Skiplagged + Cocha)
  - **Falabella Viajes / Despegar**: Camoufox + **egress Cloudflare WARP** cada ~30 min, 100% automático
- Detecta **bajadas de precio ≥ 0,5%** y las registra (alertas + correo opcional).
- Publica `data.json` solo cuando hay cambios; la web se auto-refresca cada 60 s.

## Estructura
| Archivo | Rol |
|---|---|
| `index.html` | Dashboard completo (Vue 3 por CDN, sin build) |
| `scraper.py` | Orquestador: todas las fuentes + alertas + `data.json` |
| `fetch_otas.py` | Módulo stealth (Camoufox + WARP) para Falabella/Despegar |
| `warp_boot.sh` | Bootstrap Cloudflare WARP→SOCKS5 en Actions (wgcf + wireproxy) |
| `probe_waf.py` / `diag_otas.py` | Diagnóstico del WAF DataDome |
| `.github/workflows/refresh.yml` | Ciclo cada ~5 min con auto-encadenado |
| `verify.js` | Arnés de regresión (207 checks) — `node verify.js index.html data.json` |

## 🤖 Falabella/Despegar 100% automático (sin PC, sin pagos)
DataDome (el WAF de estas OTAs) puntúa la **reputación de IP** y las de datacenter
(GitHub Actions/Azure) las penaliza. La solución corre completa en Actions:

1. `warp_boot.sh` registra un dispositivo en **Cloudflare WARP** (el VPN gratuito
   de Cloudflare, millones de usuarios reales) usando los proyectos OSS
   [`ViRb3/wgcf`](https://github.com/ViRb3/wgcf) + [`windtf/wireproxy`](https://github.com/windtf/wireproxy),
   y lo deja como proxy SOCKS5 local (`127.0.0.1:40000`).
2. El egress del runner pasa de "Azure datacenter" a **IP de consumidor WARP**,
   con reputación normal para DataDome.
3. Camoufox (Firefox stealth, `humanize` + `geoip`) navega por ese túnel;
   si DataDome insiste con challenge, queda screenshot+HTML como artifact
   (`ota-diag-*`) y el ciclo reintenta (3 fases por página).
4. Escaneo espaciado (~30 min, 4 páginas/run) y bajo volumen para no quemar IP.
5. Si un día WARP fallara, el modo oportunista anterior sigue como red de
   seguridad y la web muestra el último dato real con su marca de frescura.

**Alternativas evaluadas** (por qué se descartaron): APIs de pago tipo ZenRows/
ScrapFly/Bright Data (funcionan pero son suscripciones), FlareSolverr (solo
Cloudflare, no DataDome), solve del slider DataDome con OpenCV
(`Datadome-GeeTest-Captcha-Solver`) como plan futuro, proxies públicos gratis
(reputación peor que Azure). WARP+Camoufox es la única combinación 100% libre,
automática y sostenible para un repo público.

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
3. DataDome combina IP + fingerprint TLS + señales JS del navegador. Camoufox
   cubre fingerprint/JS; **WARP cubre la reputación de IP** (consumidor vs datacenter).
4. El challenge interactivo (slider) queda documentado con screenshots como
   artifacts de Actions para iterar el auto-solve si alguna vez fuera necesario.
