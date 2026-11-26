import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def _prepare_netscape_cookies(cookie_path: Path) -> Path | None:
    """Validates Netscape cookies or auto-converts JSON cookie exports to Netscape format."""
    try:
        raw = cookie_path.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return None
    if not raw:
        return None

    out_tmp = Path(tempfile.gettempdir()) / "ytdlp_cookies.txt"

    if raw.startswith("["):
        try:
            items = json.loads(raw)
            if isinstance(items, list):
                netscape_lines = ["# Netscape HTTP Cookie File"]
                for c in items:
                    if not isinstance(c, dict):
                        continue
                    domain = str(c.get("domain") or "").strip()
                    name = str(c.get("name") or "").strip()
                    value = str(c.get("value") or "")
                    if not domain or not name:
                        continue
                    subdomains = "TRUE" if domain.startswith(".") else "FALSE"
                    path = str(c.get("path") or "/")
                    secure = "TRUE" if c.get("secure") else "FALSE"
                    exp_raw = c.get("expirationDate") or c.get("expiry") or 0
                    try:
                        expiry = str(int(float(exp_raw)))
                    except Exception:
                        expiry = "0"
                    netscape_lines.append(
                        f"{domain}\t{subdomains}\t{path}\t{secure}\t{expiry}\t{name}\t{value}"
                    )
                if len(netscape_lines) > 1:
                    out_tmp.write_text("\n".join(netscape_lines) + "\n", encoding="utf-8")
                    return out_tmp
        except Exception:
            return None

    valid_rows = 0
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if len(s.split("\t")) >= 7:
            valid_rows += 1
    if valid_rows > 0:
        out_tmp.write_text(raw + "\n", encoding="utf-8")
        return out_tmp

    return None


def download_video_from_url(url: str, output_mp4_path: str) -> str:
    """Downloads a YouTube/Twitch/social video as MP4 using yt-dlp."""
    out_file = Path(output_mp4_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    if out_file.exists() and out_file.stat().st_size > 0:
        return str(out_file)

    clean_url = url.strip()
    if re.search(r"youtube\.com/(@|channel/|c/|user/)", clean_url, re.IGNORECASE):
        raise ValueError("Please paste a single YouTube video or Shorts link, not a channel URL.")
    shorts_match = re.search(r"youtube\.com/shorts/([A-Za-z0-9_-]{11})", clean_url)
    if shorts_match:
        clean_url = f"https://www.youtube.com/watch?v={shorts_match.group(1)}"

    base_cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--force-ipv4",
        "-f",
        "bv*[height<=1080]+ba/b[height<=1080]/best", "--remote-components", "ejs:npm",
        "--merge-output-format",
        "mp4",
        "--no-playlist",
    ]

    writable_cookies = None
    cookie_candidates = [
        os.getenv("YTDLP_COOKIES_FILE", ""),
        "/etc/secrets/cookies.txt",
        "cookies.txt",
        "backend/cookies.txt",
    ]
    for candidate in cookie_candidates:
        if candidate and Path(candidate).is_file():
            prepared = _prepare_netscape_cookies(Path(candidate))
            if prepared:
                writable_cookies = prepared
                break
            else:
                print(f"[media_service] Skipping invalid cookies file: {candidate}")

    attempts = []
    if writable_cookies:
        attempts.append(base_cmd + ["--cookies", str(writable_cookies), "-o", str(out_file), clean_url])
    attempts.append(base_cmd + ["-o", str(out_file), clean_url])
    attempts.append(
        base_cmd + ["--extractor-args", "youtube:player_client=android,web", "-o", str(out_file), clean_url]
    )

    last_detail = "Unknown yt-dlp error"
    for cmd in attempts:
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            if out_file.exists() and out_file.stat().st_size > 0:
                return str(out_file)
        except subprocess.CalledProcessError as exc:
            err_lines = [line.strip() for line in (exc.stderr or "").splitlines() if line.strip()]
            last_detail = err_lines[-1] if err_lines else str(exc)

    raise RuntimeError(f"YouTube download failed: {last_detail}")


def probe_video(video_path: str) -> dict:
    """Inspects video duration, resolution, and streams via ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration,size:stream=codec_type,width,height,r_frame_rate",
        "-of",
        "json",
        video_path,
    ]
    res = subprocess.run(cmd, check=True, capture_output=True, text=True)
    data = json.loads(res.stdout)

    video_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"), {}
    )
    fmt = data.get("format", {})

    return {
        "duration": float(fmt.get("duration", 0.0)),
        "size_bytes": int(fmt.get("size", 0)),
        "width": int(video_stream.get("width", 1920)),
        "height": int(video_stream.get("height", 1080)),
        "fps": video_stream.get("r_frame_rate", "30/1"),
    }


def extract_audio_wav(video_path: str, wav_output_path: str) -> str:
    """Extracts 16kHz mono 16-bit PCM WAV audio for Faster-Whisper."""
    out_file = Path(wav_output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(out_file),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return str(out_file)