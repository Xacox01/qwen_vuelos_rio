# ✈️ Radar Río — Comparador en vivo SCL ⇄ Río de Janeiro + playas LatAm

Sitio público en **GitHub Pages**: `https://Xacox01.github.io/vuelos-rio-live/`
Verano 2027 (ene–feb) · viaje ≥ 9 noches · regreso ≤ 23-feb · grupo de hasta 9 pasajeros.

## Qué hace
- **Cada ~5 min** (GitHub Actions) consulta en vivo:
  - **APIs oficiales**: JetSMART (availability), SKY (Sputnik), LATAM (BFF calendario)
  - **Cocha** (cotización real por API GraphQL) y **Skiplagged** (metasearch global)
  - **Playas LatAm**: 16 destinos bajo $500.000 pp (Skiplagged + Cocha)
  - **Falabella Viajes / Despegar**: Camoufox + **egress Cloudflare WARP** cada ~30 min, 100% automático
- **Cupones activos**: `fetch_coupons.py` revisa cupon.cl (JetSMART, LATAM, Despegar,
  Cocha, Viajes Falabella) cada 6 h; resuelve los **códigos reales** vía el endpoint
  AJAX `/modals/coupon_clickout` y los guarda en `data.json["cupones"]`.
- Detecta **bajadas de precio ≥ 0,5%** y las registra (alertas + correo opcional).
- Publica `data.json` solo cuando hay cambios; la web se auto-refresca cada 60 s.

## Estructura
| Archivo | Rol |
|---|---|
| `index.html` | Sitio completo (Vue 3 por CDN, sin build) — diseño plano estilo OTA (blanco + azul `#0062e3` + naranja `#ff690f`) |
| `scraper.py` | Orquestador: todas las fuentes + alertas + `data.json` (preserva la sección `cupones`) |
| `fetch_otas.py` | Módulo stealth (Camoufox + WARP) para Falabella/Despegar |
| `fetch_coupons.py` | Recopilador de cupones reales de cupon.cl (códigos base64 incluidos) |
| `warp_boot.sh` | Bootstrap Cloudflare WARP→SOCKS5 en Actions (wgcf + wireproxy) |
| `probe_waf.py` / `diag_otas.py` | Diagnóstico del WAF DataDome |
| `assets/dest/*.jpg` | Fotos de los 16 destinos (Wikipedia Commons, ver `CREDITS.txt`) |
| `.github/workflows/refresh.yml` | Ciclo cada ~5 min con auto-encadenado + paso de cupones (gate 6 h) |
| `verify.js` | Arnés de regresión (54 checks) — `node verify.js index.html data.json` |

## 🎟️ Cupones automáticos
1. Se rastrean 5 tiendas de cupon.cl (`/jetsmart`, `/latam`, `/despegar-com`,
   `/cocha`, `/falabella`) con `requests` plano — sin navegador, ideal para CI.
2. Cada cupón aporta: título, % o monto, descripción, condiciones (mínimo, validez,
   medios de pago), estado "verificado" y enlace de clickout.
3. Los de tipo `coupon` resuelven su **código real** consultando
   `cupon.cl/modals/coupon_clickout?id=<id>` (texto plano o base64).
4. Filtrado de relevancia de viaje para tiendas retail (Falabella) y dedupe por título.
5. Degradación suave: si cupon.cl falla, se conserva el último set y se marca `stale`.

## 🤖 Falabella/Despegar 100% automático (sin PC, sin pagos)
DataDome (el WAF de estas OTAs) puntúa la **reputación de IP** y las de datacenter
(GitHub Actions/Azure) las penaliza. La solución corre completa en Actions:

1. `warp_boot.sh` registra un dispositivo en **Cloudflare WARP** usando los proyectos OSS
   [`ViRb3/wgcf`](https://github.com/ViRb3/wgcf) + [`windtf/wireproxy`](https://github.com/windtf/wireproxy),
   y lo deja como proxy SOCKS5 local (`127.0.0.1:40000`).
2. El egress del runner pasa de "Azure datacenter" a **IP de consumidor WARP**,
   con reputación normal para DataDome.
3. Camoufox (Firefox stealth, `humanize` + `geoip`) navega por ese túnel;
   si DataDome insiste con challenge, queda screenshot+HTML como artifact
   (`ota-diag-*`) y el ciclo reintenta (3 fases por página).
4. Escaneo espaciado (~30 min, 4 páginas/run) y bajo volumen para no quemar IP.

**Alternativas evaluadas** (por qué se descartaron): APIs de pago tipo ZenRows/
ScrapFly/Bright Data (funcionan pero son suscripciones), FlareSolverr (solo
Cloudflare, no DataDome), solve del slider DataDome con OpenCV
(`Datadome-GeeTest-Captcha-Solver`) como plan futuro, proxies públicos gratis
(reputación peor que Azure). WARP+Camoufox es la única combinación 100% libre,
automática y sostenible para un repo público.

## 💰 Monetización (listo para activar)
- Todos los enlaces salientes llevan `rel="nofollow"` y están centralizados en los
  computeds de Vue — cambiar un enlace por su versión de afiliado es un cambio de una línea.
- El footer ya incluye la **divulgación de afiliados** (requisito legal en la mayoría
  de jurisdicciones y de las redes de afiliados).
- Programas compatibles con este tipo de sitio: Despegar Partner Network,
  programática de Viajes Falabella, Cocha afiliados, Skiplagged no tiene — usar
  deep-links de aerolíneas. Sugerencia: pedir a la red elegida los parámetros UTM
  y sustituirlos en `filas`/`tiles`/`cupones`.

## Administración
- Refresco manual: repo → **Actions** → *refresh* → **Run workflow**.
- Cambiar cadencia del escaneo OTA: variable de entorno `OTAS_GAP` (segundos, default 1800).
- Cambiar cadencia de cupones: valor `21600` (segundos) en `refresh.yml`.
- Ver historial: Actions → refresh (cada corrida deja el detalle de fuentes).
- Costo: $0 (repo público).
