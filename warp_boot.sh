#!/usr/bin/env bash
# Bootstrap de Cloudflare WARP como proxy SOCKS5 local para las OTAs (DataDome).
#
# ¿Por qué? DataDome puntúa la reputación de IP: las de GitHub Actions (Azure,
# datacenter) son penalizadas, mientras que el egress de WARP (IP de consumidor
# compartida por millones de usuarios reales del VPN gratuito de Cloudflare)
# conserva buena reputación. Proyectos OSS usados:
#   - ViRb3/wgcf            (registro de dispositivo WARP, genera perfil WireGuard)
#   - windtf/wireproxy      (WireGuard en userspace -> SOCKS5, sin root, ideal CI)
#
# Idempotente y a prueba de fallos: si WARP no logra levantarse, exporta
# WARP_OK=0 y el resto del pipeline sigue (escaneo OTA oportunista como antes).
set -u
: "${GITHUB_ENV:=/dev/null}"

WGCF_VER="2.3.1"
WIREPROXY_VER="1.1.3"
WDIR="$HOME/.warp"
SOCK_PORT=40000
mkdir -p "$WDIR"
cd "$WDIR" || exit 0

echo "== WARP bootstrap (egress de reputación consumidor para OTAs) =="

# ---------- 1) binarios (cacheados por actions/cache en ~/.warp) ----------
if [ ! -x ./wgcf ]; then
  curl -fsSL "https://github.com/ViRb3/wgcf/releases/download/v${WGCF_VER}/wgcf_${WGCF_VER}_linux_amd64" -o wgcf \
    && chmod +x wgcf || echo "::warning::no se pudo descargar wgcf"
fi
if [ ! -x ./wireproxy ]; then
  curl -fsSL "https://github.com/windtf/wireproxy/releases/download/v${WIREPROXY_VER}/wireproxy_linux_amd64.tar.gz" -o wp.tgz \
    && tar xzf wp.tgz wireproxy 2>/dev/null && chmod +x wireproxy || echo "::warning::no se pudo descargar wireproxy"
fi
if [ ! -x ./wgcf ] || [ ! -x ./wireproxy ]; then
  echo "WARP_OK=0" >> "$GITHUB_ENV"
  exit 0
fi

# ---------- 2) cuenta WARP (dispositivo único reutilizado entre corridas) ----------
if [ ! -s wgcf-account.toml ]; then
  echo "Registrando dispositivo WARP nuevo..."
  ./wgcf register --accept-tos --config wgcf-account.toml > reg.log 2>&1 \
    || { echo "::warning::registro WARP falló: $(tail -1 reg.log)"; echo "WARP_OK=0" >> "$GITHUB_ENV"; exit 0; }
fi
./wgcf generate --config wgcf-account.toml > gen.log 2>&1 || { echo "WARP_OK=0" >> "$GITHUB_ENV"; exit 0; }

# ---------- 3) levantar wireproxy probando endpoints de fallback ----------
mkconf() {  # $1 = endpoint
  cat > wireproxy.conf <<EOF
[Interface]
PrivateKey = $(sed -n 's/^PrivateKey = //p' wgcf-profile.conf | head -1)
Address = 172.16.0.2/32
DNS = 1.1.1.1
MTU = 1280

[Peer]
PublicKey = bmXOC+F1FxEMF9dyiK2H5/1SUtzH0JuVo51h2wPfgyo=
AllowedIPs = 0.0.0.0/0
Endpoint = $1

[Socks5]
BindAddress = 127.0.0.1:${SOCK_PORT}
EOF
}

handshake_ok() {  # ¿el túnel ya pasa tráfico?
  local trace
  trace=$(curl -s --max-time 6 --socks5-hostname "127.0.0.1:${SOCK_PORT}" \
    "https://www.cloudflare.com/cdn-cgi/trace" 2>/dev/null || true)
  echo "$trace" | grep -q "warp=on" || return 1
  WARP_EGRESS_IP=$(echo "$trace" | sed -n 's/^ip=//p' | head -1)
  return 0
}

ENDPOINTS=("engage.cloudflareclient.com:2408" "162.159.192.1:2408" "162.159.193.1:2408" "162.159.192.2:443")
WARP_EGRESS_IP=""
for ep in "${ENDPOINTS[@]}"; do
  echo "Probando endpoint WARP: $ep"
  pkill -f wireproxy 2>/dev/null || true
  mkconf "$ep"
  nohup ./wireproxy -c wireproxy.conf > wireproxy.log 2>&1 &
  sleep 6
  for i in $(seq 1 12); do   # hasta ~42 s para completar handshake
    if handshake_ok; then break 2; fi
    sleep 3
  done
done

if [ -n "$WARP_EGRESS_IP" ]; then
  echo "WARP activo — egress IP: $WARP_EGRESS_IP"
  {
    echo "OTA_PROXY=socks5://127.0.0.1:${SOCK_PORT}"
    echo "WARP_OK=1"
    echo "WARP_IP=${WARP_EGRESS_IP}"
  } >> "$GITHUB_ENV"
else
  echo "::warning::WARP no pudo levantarse; escaneo OTA seguirá en modo oportunista"
  echo "WARP_OK=0" >> "$GITHUB_ENV"
  pkill -f wireproxy 2>/dev/null || true
fi
exit 0
