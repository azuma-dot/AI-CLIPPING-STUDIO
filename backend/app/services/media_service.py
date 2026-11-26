import json
import subprocess
import sys
from pathlib import Path


def download_video_from_url(url: str, output_mp4_path: str) -> str:
    """Downloads a YouTube/Twitch/social video as MP4 using yt-dlp."""
    out_file = Path(output_mp4_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    if out_file.exists():
        return str(out_file)

    import os
    import re
    import shutil
    import tempfile

    clean_url = url.strip()
    if re.search(r"youtube\.com/(@|channel/|c/|user/)", clean_url, re.IGNORECASE):
        raise ValueError("Please paste a single YouTube video or Shorts link, not a channel URL.")
    shorts_match = re.search(r"youtube\.com/shorts/([A-Za-z0-9_-]{11})", clean_url)
    if shorts_match:
        clean_url = f"https://www.youtube.com/watch?v={shorts_match.group(1)}"

    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--force-ipv4",
        "-f",
        "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b[height<=1080]",
        "--merge-output-format",
        "mp4",
        "--no-playlist",
    ]

    cookie_candidates = [
        os.getenv("YTDLP_COOKIES_FILE", ""),
        "/etc/secrets/cookies.txt",
        "cookies.txt",
    ]
    for candidate in cookie_candidates:
        if candidate and Path(candidate).is_file():
            writable_cookies = Path(tempfile.gettempdir()) / "ytdlp_cookies.txt"
            shutil.copy2(candidate, writable_cookies)
            cmd.extend(["--cookies", str(writable_cookies)])
            break

    cmd.extend(["-o", str(out_file), clean_url])

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        err_lines = [line.strip() for line in (exc.stderr or "").splitlines() if line.strip()]
        detail = err_lines[-1] if err_lines else str(exc)
        raise RuntimeError(f"YouTube download failed: {detail}") from exc
    if not out_file.exists():
        raise FileNotFoundError(f"yt-dlp did not produce expected file: {out_file}")
    return str(out_file)


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
