import json
from dataclasses import dataclass
from pathlib import Path
from temporalio import activity
from app.services.auth_service import get_runtime_config
from app.services.media_service import (
    download_video_from_url,
    probe_video,
    extract_audio_wav,
)
from app.services.transcription_service import transcribe_wav_to_json
from app.services.highlight_service import detect_highlights
from app.services.render_service import render_vertical_clip, launch_in_capcut


@dataclass
class ClipJobInput:
    project_id: str
    source_path: str = ""
    prompt: str = "Edit like a viral short-form editor"
    source_url: str = ""
    clip_duration: int = 30
    aspect_ratio: str = "9:16"
    captions_enabled: bool = True
    auto_capcut: bool = False


@activity.defn
async def inspect_and_extract_audio(job: ClipJobInput) -> dict:
    """Activity 1: Downloads URL if needed, probes video, and extracts 16kHz WAV."""
    source_path = job.source_path
    if job.source_url and (not source_path or not Path(source_path).exists()):
        target_mp4 = str(Path(str(next((p for p in Path(__file__).resolve().parents if (p / "frontend").exists()), Path(__file__).resolve().parents[2]) / "storage/uploads")) / f"{job.project_id}.mp4")
        activity.logger.info(f"[{job.project_id}] Downloading URL: {job.source_url}")
        source_path = download_video_from_url(job.source_url, target_mp4)

    activity.logger.info(f"[{job.project_id}] Probing media: {source_path}")
    metadata = probe_video(source_path)

    wav_path = str(Path(str(next((p for p in Path(__file__).resolve().parents if (p / "frontend").exists()), Path(__file__).resolve().parents[2]) / "storage/temp")) / f"{job.project_id}.wav")
    activity.logger.info(f"[{job.project_id}] Extracting audio to: {wav_path}")
    extract_audio_wav(source_path, wav_path)

    return {
        "project_id": job.project_id,
        "source_path": source_path,
        "prompt": job.prompt,
        "wav_path": wav_path,
        "metadata": metadata,
        "clip_duration": int(job.clip_duration or 30),
        "aspect_ratio": (job.aspect_ratio or "9:16").upper(),
        "captions_enabled": bool(job.captions_enabled),
        "auto_capcut": bool(job.auto_capcut),
    }


@activity.defn
async def transcribe_audio(media_info: dict) -> dict:
    """Activity 2: Runs local Faster-Whisper transcription using Admin-configured model size."""
    cfg = get_runtime_config()
    project_id = media_info["project_id"]
    wav_path = media_info["wav_path"]
    transcript_path = str(
        Path(str(next((p for p in Path(__file__).resolve().parents if (p / "frontend").exists()), Path(__file__).resolve().parents[2]) / "storage/temp")) / f"{project_id}_transcript.json"
    )

    activity.logger.info(f"[{project_id}] Transcribing audio (Whisper: {cfg['whisper_model']})...")
    transcript = transcribe_wav_to_json(
        wav_path=wav_path,
        output_json_path=transcript_path,
        model_size=cfg["whisper_model"],
    )

    return {
        "project_id": project_id,
        "transcript_path": transcript_path,
        "language": transcript["language"],
        "segment_count": transcript["segment_count"],
        "full_text": transcript["full_text"],
    }


@activity.defn
async def detect_viral_highlights(payload: dict) -> dict:
    """Activity 3: Uses Admin-configured Ollama LLM model to identify viral clips."""
    cfg = get_runtime_config()
    media_info = payload["media_info"]
    transcript_info = payload["transcript_info"]
    project_id = media_info["project_id"]
    target_duration = int(media_info.get("clip_duration", 30))

    transcript = json.loads(
        Path(transcript_info["transcript_path"]).read_text(encoding="utf-8")
    )
    duration_sec = float(media_info["metadata"].get("duration", 10.0))
    highlights_path = str(
        Path(str(next((p for p in Path(__file__).resolve().parents if (p / "frontend").exists()), Path(__file__).resolve().parents[2]) / "storage/temp")) / f"{project_id}_highlights.json"
    )

    activity.logger.info(
        f"[{project_id}] Detecting ~{target_duration}s highlights (Model: {cfg['ollama_model']})..."
    )
    clips = detect_highlights(
        transcript=transcript,
        duration_sec=duration_sec,
        user_prompt=media_info.get("prompt", "Edit like a viral short-form editor"),
        output_json_path=highlights_path,
        model=cfg["ollama_model"],
        max_clips=int(cfg["max_clips"]),
        target_duration=target_duration,
    )

    return {
        "project_id": project_id,
        "highlights_path": highlights_path,
        "clip_count": len(clips),
        "clips": clips,
    }


@activity.defn
async def render_viral_clips(payload: dict) -> dict:
    """Activity 4: Renders clips with requested aspect ratio, captions, punch-zooms, and Shorts CapCut handoff."""
    media_info = payload["media_info"]
    transcript_info = payload["transcript_info"]
    highlights_info = payload["highlights_info"]
    project_id = media_info["project_id"]

    req_ratio = str(media_info.get("aspect_ratio", "9:16")).upper()
    meta = media_info.get("metadata", {})
    src_w = int(meta.get("width", 1920))
    src_h = int(meta.get("height", 1080))
    if req_ratio == "AUTO":
        effective_ratio = "9:16" if src_h > src_w else "16:9"
    elif req_ratio in ("9:16", "16:9"):
        effective_ratio = req_ratio
    else:
        effective_ratio = "9:16"

    captions_enabled = bool(media_info.get("captions_enabled", True))
    auto_capcut = bool(media_info.get("auto_capcut", False))

    transcript = json.loads(
        Path(transcript_info["transcript_path"]).read_text(encoding="utf-8")
    )

    rendered = []
    for clip in highlights_info["clips"]:
        activity.logger.info(
            f"[{project_id}] Rendering clip {clip['clip_index']} ({effective_ratio}, captions={captions_enabled})..."
        )
        result = render_vertical_clip(
            source_video_path=media_info["source_path"],
            transcript=transcript,
            clip=clip,
            project_id=project_id,
            temp_dir=str(next((p for p in Path(__file__).resolve().parents if (p / "frontend").exists()), Path(__file__).resolve().parents[2]) / "storage/temp"),
            output_dir=str(next((p for p in Path(__file__).resolve().parents if (p / "frontend").exists()), Path(__file__).resolve().parents[2]) / "storage/outputs"),
            aspect_ratio=effective_ratio,
            captions_enabled=captions_enabled,
        )
        rendered.append(result)

    if effective_ratio == "9:16" and auto_capcut and rendered:
        capcut_target = rendered[0].get("capcut_source_path") or rendered[0]["output_path"]
        launch_res = launch_in_capcut(capcut_target)
        activity.logger.info(f"[{project_id}] Auto-CapCut Shorts handoff: {launch_res}")

    manifest_path = Path(str(next((p for p in Path(__file__).resolve().parents if (p / "frontend").exists()), Path(__file__).resolve().parents[2]) / "storage/outputs")) / project_id / "manifest.json"
    manifest_path.write_text(json.dumps(rendered, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "project_id": project_id,
        "manifest_path": str(manifest_path),
        "rendered_clips": rendered,
    }
