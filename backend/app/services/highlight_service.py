import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
DEFAULT_MODEL = "qwen3.5:4b"


def _ensure_ollama_running() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as r:
            if r.status == 200:
                return True
    except Exception:
        pass

    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for _ in range(10):
            time.sleep(1)
            try:
                with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as r:
                    if r.status == 200:
                        return True
            except Exception:
                continue
    except Exception:
        return False
    return False


def _extract_json_object(raw_text: str) -> dict:
    cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in LLM output: {cleaned[:200]}")
    return json.loads(match.group(0))


def _clamp_clip_duration(start: float, end: float, duration_sec: float, target_duration: int) -> tuple[float, float]:
    target = float(max(10, target_duration))
    max_allowed = target + 5.0
    min_allowed = max(6.0, target - 5.0)

    start = max(0.0, min(start, max(0.0, duration_sec - 2.0)))
    end = min(duration_sec, max(end, start + 2.0))

    if (end - start) > max_allowed:
        end = min(duration_sec, start + target)
    elif (end - start) < min_allowed and duration_sec >= min_allowed:
        needed = target - (end - start)
        new_end = min(duration_sec, end + needed)
        rem = target - (new_end - start)
        new_start = max(0.0, start - rem) if rem > 0 else start
        start, end = new_start, new_end

    return round(start, 3), round(max(end, start + 1.0), 3)


def detect_highlights(
    transcript: dict,
    duration_sec: float,
    user_prompt: str,
    output_json_path: str,
    model: str = DEFAULT_MODEL,
    max_clips: int = 3,
    target_duration: int = 30,
) -> list[dict]:
    """Uses local Ollama model to select viral clip timestamps and hooks matching target_duration."""
    out_file = Path(output_json_path)
    if out_file.exists():
        return json.loads(out_file.read_text(encoding="utf-8"))

    target_duration = int(target_duration) if int(target_duration) in (15, 30, 60) else 30
    segments = transcript.get("segments", [])
    if not segments:
        s0, e0 = _clamp_clip_duration(0.0, min(duration_sec, float(target_duration)), duration_sec, target_duration)
        clips = [
            {
                "clip_index": 1,
                "start": s0,
                "end": e0,
                "title": "Highlight Clip 1",
                "hook_caption": "WATCH UNTIL THE END",
                "virality_score": 75,
                "reason": "Fallback full-segment highlight.",
            }
        ]
        out_file.write_text(json.dumps(clips, indent=2), encoding="utf-8")
        return clips

    formatted_lines = [
        f"[{seg['start']:.2f}s - {seg['end']:.2f}s] {seg['text']}"
        for seg in segments
    ]
    transcript_block = "\n".join(formatted_lines)

    min_win = max(8, target_duration - 5)
    max_win = target_duration + 5
    system_msg = (
        "You are an expert viral short-form video editor (TikTok, Reels, YouTube Shorts). "
        f"Analyze the timestamped transcript and pick the 1 to {max_clips} strongest self-contained viral moments "
        f"matching the user's editing style prompt. "
        f"STRICT DURATION RULE: Each clip MUST be approximately {target_duration} seconds long "
        f"(strictly between {min_win}s and {max_win}s, unless the entire source video is shorter than {target_duration}s). "
        "Return ONLY valid JSON with key 'clips': a list of objects containing "
        "'start' (float seconds), 'end' (float seconds), 'title' (catchy 4-8 word title), "
        "'hook_caption' (2-5 uppercase words for thumbnail/hook), 'virality_score' (1-100), and 'reason' (1 sentence)."
    )
    user_msg = (
        f"Target clip duration: {target_duration} seconds\n"
        f"Editing style prompt: {user_prompt}\n"
        f"Total video duration: {duration_sec:.2f}s\n\n"
        f"Timestamped Transcript:\n{transcript_block}"
    )

    clips: list[dict] = []
    if _ensure_ollama_running():
        payload = {
            "model": model,
            "stream": False,
            "think": False,
            "format": "json",
            "options": {"temperature": 0.3},
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
        }
        req = urllib.request.Request(
            OLLAMA_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw_content = data.get("message", {}).get("content", "")
                parsed = _extract_json_object(raw_content)
                for idx, item in enumerate(parsed.get("clips", [])[:max_clips], start=1):
                    raw_s = float(item.get("start", 0.0))
                    raw_e = float(item.get("end", raw_s + target_duration))
                    if raw_e - raw_s < 2.0:
                        raw_s = float(segments[0]["start"])
                        raw_e = min(duration_sec, raw_s + float(target_duration))
                    start, end = _clamp_clip_duration(raw_s, raw_e, duration_sec, target_duration)
                    clips.append(
                        {
                            "clip_index": idx,
                            "start": start,
                            "end": end,
                            "title": str(item.get("title", f"Viral Clip {idx}")).strip(),
                            "hook_caption": str(item.get("hook_caption", "INSANE MOMENT")).strip().upper(),
                            "virality_score": int(item.get("virality_score", 85)),
                            "reason": str(item.get("reason", "High emotional hook and payoff.")).strip(),
                        }
                    )
        except Exception as exc:
            print(f"[highlight_service] Ollama fallback triggered: {exc}")

    if not clips:
        raw_s = float(segments[0]["start"])
        raw_e = min(duration_sec, float(segments[-1]["end"]) + 0.3)
        start, end = _clamp_clip_duration(raw_s, raw_e, duration_sec, target_duration)
        clips = [
            {
                "clip_index": 1,
                "start": start,
                "end": end,
                "title": "Clip of the Year Moment",
                "hook_caption": "INSANE CLUTCH",
                "virality_score": 88,
                "reason": "High-energy reaction captured directly from transcript.",
            }
        ]

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(clips, indent=2, ensure_ascii=False), encoding="utf-8")
    return clips
