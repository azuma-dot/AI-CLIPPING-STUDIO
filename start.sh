#!/bin/sh
set -e

if [ -f /opt/bgutil/server/build/main.js ]; then
  echo "[BOOT] Starting bgutil PO token server on :4416..."
  node /opt/bgutil/server/build/main.js --port 4416 &
fi

echo "[BOOT] Setting up Cloudflare WARP (wireproxy)..."
mkdir -p /tmp/warp
WARP_OK=0
if [ ! -f /tmp/warp/wgcf-profile.conf ]; then
  (cd /tmp/warp && wgcf register --accept-tos && wgcf generate) || true
fi

if [ -f /tmp/warp/wgcf-profile.conf ]; then
  if ! grep -q '^\[Socks5\]' /tmp/warp/wgcf-profile.conf; then
    printf '\n[Socks5]\nBindAddress = 127.0.0.1:1080\n' >> /tmp/warp/wgcf-profile.conf
  fi
  wireproxy -c /tmp/warp/wgcf-profile.conf -s &
  sleep 3
  if curl -fsS --max-time 10 --proxy socks5h://127.0.0.1:1080 https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null | grep -q 'warp=on'; then
    WARP_OK=1
    echo "[BOOT] Cloudflare WARP proxy ACTIVE on socks5://127.0.0.1:1080"
  else
    echo "[BOOT] WARNING: WARP proxy check failed; continuing direct"
  fi
else
  echo "[BOOT] WARNING: wgcf registration failed; continuing direct"
fi

mkdir -p /tmp/ytdlp-cfg/yt-dlp
if [ "$WARP_OK" = "1" ]; then
  printf '%s\n' '--proxy socks5://127.0.0.1:1080' '--remote-components ejs:github' > /tmp/ytdlp-cfg/yt-dlp/config
else
  printf '%s\n' '--remote-components ejs:github' > /tmp/ytdlp-cfg/yt-dlp/config
fi
cp /tmp/ytdlp-cfg/yt-dlp/config /etc/yt-dlp.conf 2>/dev/null || true
export XDG_CONFIG_HOME=/tmp/ytdlp-cfg

echo "[BOOT] Launching backend..."
exec "$@"