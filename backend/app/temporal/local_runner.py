import asyncio
import json
import threading
import traceback
from pathlib import Path

from app.temporal.activities import (
    ClipJobInput,
    inspect_and_extract_audio,
    transcribe_audio,
    detect_viral_highlights,
    render_viral_clips,
)


def _temp_dir() -> Path:
    root = next(
        (p for p in Path(__file__).resolve().parents if (p / "frontend").exists()),
        Path(__file__).resolve().parents[2],
    )
    d = root / "storage" / "temp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _status_file(project_id: str) -> Path:
    return _temp_dir() / f"{project_id}_local_status.json"


def _write_status(project_id: str, status: str, stage: str = "", error: str = "") -> None:
    payload = {"status": status, "stage": stage, "error": error}
    _status_file(project_id).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def get_local_job_status(project_id: str) -> dict | None:
    p = _status_file(project_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


async def _run_pipeline(job: ClipJobInput) -> None:
    pid = job.project_id
    try:
        _write_status(pid, "RUNNING", stage="Downloading & extracting audio")
        media_info = await inspect_and_extract_audio(job)

        _write_status(pid, "RUNNING", stage="Transcribing audio")
        transcript_info = await transcribe_audio(media_info)

        _write_status(pid, "RUNNING", stage="Detecting viral highlights")
        highlights_info = await detect_viral_highlights(
            {"media_info": media_info, "transcript_info": transcript_info}
        )

        _write_status(pid, "RUNNING", stage="Rendering clips")
        await render_viral_clips(
            {
                "media_info": media_info,
                "transcript_info": transcript_info,
                "highlights_info": highlights_info,
            }
        )

        _write_status(pid, "COMPLETED", stage="Done")
    except Exception as exc:
        traceback.print_exc()
        _write_status(pid, "FAILED", stage="Failed", error=str(exc))


def start_local_clip_job(job: ClipJobInput) -> None:
    _write_status(job.project_id, "RUNNING", stage="Queued")

    def _worker() -> None:
        asyncio.run(_run_pipeline(job))

    t = threading.Thread(target=_worker, name=f"local-clip-{job.project_id}", daemon=True)
    t.start()