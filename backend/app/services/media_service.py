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
    """Downloads a video using yt-dlp with bounded network waits."""
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
        sys.executable, "-m", "yt_dlp",
        "--force-ipv4",
        "--socket-timeout", "30",
        "--retries", "3",
        "--fragment-retries", "3",
        "--newline",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/best",
        "--remote-components", "ejs:npm",
        "--extractor-args",
        "youtube:player-client=mweb",
        "--merge-output-format", "mp4",
        "--no-playlist",
    ]

    writable_cookies = None

    disable_cookies = (
        os.getenv("YTDLP_DISABLE_COOKIES", "").strip().lower()
        in {"1", "true", "yes"}
    )

    cookie_candidates = [] if disable_cookies else [
        os.getenv("YTDLP_COOKIES_FILE", ""),
        "/etc/secrets/cookies.txt",
        "cookies.txt",
        "backend/cookies.txt",
    ]

    if disable_cookies:
        print("[media_service] Cookie usage disabled by configuration.")

    for candidate in cookie_candidates:
        if candidate and Path(candidate).is_file():
            prepared = _prepare_netscape_cookies(Path(candidate))
            if prepared:
                writable_cookies = prepared
                break
            print(f"[media_service] Skipping invalid cookies file: {candidate}")


    cmd = base_cmd.copy()

    if writable_cookies:
        print(f"[media_service] Using cookies from: {writable_cookies}")
        cmd.extend(["--cookies", str(writable_cookies)])
    else:
        print("[media_service] No valid cookies file found; running unauthenticated.")

    cmd.extend(["-v", "-o", str(out_file), clean_url])

    try:
        print(f"[media_service] Starting download: {clean_url}")

        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=600,
        )

        if out_file.exists() and out_file.stat().st_size > 0:
            print(f"[media_service] Download completed: {out_file}")
            return str(out_file)

        raise RuntimeError("yt-dlp finished without creating the output video.")

    except subprocess.TimeoutExpired:
        if out_file.exists():
            out_file.unlink()

        raise RuntimeError(
            "Video download timed out after 10 minutes. "
            "The source may be unavailable or the connection may be blocked."
        )

    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()

        print("[media_service] yt-dlp process failed.")

        if stdout:
            print(f"[media_service] yt-dlp stdout:\n{stdout}")

        if stderr:
            print(f"[media_service] yt-dlp stderr:\n{stderr}")

        err_lines = [
            line for line in stderr.splitlines()
            if "ERROR:" in line or "WARNING:" in line
        ]

        detail = " | ".join(err_lines[-3:]) if err_lines else (
            stderr[-500:] or str(exc)
        )

        raise RuntimeError(f"YouTube download failed: {detail}")


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
