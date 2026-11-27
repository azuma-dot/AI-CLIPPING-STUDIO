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

  if curl -fsS --max-time 10 --proxy socks5h://127.0.0.1:1080 \
    https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null | grep -q 'warp=on'; then

    echo "[BOOT] WARP tunnel verified against Cloudflare."

    YOUTUBE_WARP_STATUS="$(curl -sS --max-time 20 \
      --proxy socks5h://127.0.0.1:1080 \
      -o /dev/null -w '%{http_code}' \
      https://www.youtube.com/generate_204 2>/dev/null || true)"

    case "$YOUTUBE_WARP_STATUS" in
      [1-5][0-9][0-9])
        WARP_OK=1
        echo "[BOOT] YouTube reachable through WARP (HTTP $YOUTUBE_WARP_STATUS)."
        ;;
      *)
        echo "[BOOT] WARNING: WARP reaches Cloudflare but YouTube probe failed (HTTP ${YOUTUBE_WARP_STATUS:-000})."
        echo "[BOOT] Will test direct YouTube connectivity."
        ;;
    esac
  else
    echo "[BOOT] WARNING: WARP proxy check failed."
  fi
else
  echo "[BOOT] WARNING: wgcf registration failed."
fi

if [ "$WARP_OK" != "1" ]; then
  YOUTUBE_DIRECT_STATUS="$(curl -sS --max-time 20 --noproxy '*' \
    -o /dev/null -w '%{http_code}' \
    https://www.youtube.com/generate_204 2>/dev/null || true)"

  case "$YOUTUBE_DIRECT_STATUS" in
    [1-5][0-9][0-9])
      echo "[BOOT] YouTube reachable through direct connection (HTTP $YOUTUBE_DIRECT_STATUS)."
      ;;
    *)
      echo "[BOOT] WARNING: YouTube probe failed through both WARP and direct network."
      ;;
  esac
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