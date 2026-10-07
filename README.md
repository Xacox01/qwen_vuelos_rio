# ✈️ Vuelos Río Live — GitHub Pages

Dashboard en vivo SCL ⇄ Río de Janeiro (ene–feb 2027), 100% gratis y permanente.

## Cómo funciona
- **GitHub Actions** (`.github/workflows/refresh.yml`) corre `scraper.py` **cada 5 minutos**:
  consulta la API oficial de disponibilidad de JetSMART, la API oficial de SKY (Sputnik),
  el espejo Google/LATAM y la referencia de Viajes Falabella.
- Si los precios clave cambiaron, publica `data.json` nuevo (commit automático).
- **GitHub Pages** sirve `index.html` + `data.json`: tu web pública es
  `https://TU_USUARIO.github.io/NOMBRE_REPO/`

## Uso
1. Abre la URL de Pages. La página se auto-recarga sola cada 60 s.
2. Botones "Comprar/Verificar" abren el checkout real de cada aerolínea.

## Administración
- Refresco manual: repo → pestaña **Actions** → workflow *refresh* → **Run workflow**.
- Ver historial de corridas: Actions → refresh (útil para ver logs de cada ciclo).
- Cambiar frecuencia: edita el `cron` en `.github/workflows/refresh.yml`
  (mínimo soportado por GitHub: `*/5 * * * *`).
- Apagar todo: desactiva el schedule o elimina el repo.
- Costo: $0 (repositorio público = minutos de Actions y Pages ilimitados).

## Notas
- Falabella bloquea bots (WAF): esos datos se conservan como *referencia cacheada*
  y se muestran con aviso amarillo.
- Los commits solo ocurren cuando cambian precios → el historial git queda liviano.
