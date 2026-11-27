import json
import subprocess
import wave
from pathlib import Path
import numpy as np
from faster_whisper import WhisperModel

_MODEL_CACHE: dict[str, WhisperModel] = {}


def _detect_device() -> tuple[str, str]:
    """Uses CUDA float16 if an NVIDIA GPU is present, otherwise CPU int8."""
    try:
        subprocess.run(["nvidia-smi"], capture_output=True, check=True)
        return "cuda", "float16"
    except Exception:
        return "cpu", "int8"


def _load_wav_float32(wav_path: str) -> np.ndarray:
    """Loads 16kHz mono 16-bit PCM WAV directly into float32 numpy array (bypasses PyAV)."""
    with wave.open(wav_path, "rb") as wf:
        frames = wf.readframes(wf.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def get_whisper_model(model_size: str = "tiny") -> WhisperModel:
    if model_size not in _MODEL_CACHE:
        device, compute_type = _detect_device()
        _MODEL_CACHE[model_size] = WhisperModel("tiny",
            device=device,
            compute_type=compute_type,
        )
    return _MODEL_CACHE[model_size]


def transcribe_wav_to_json(
    wav_path: str,
    output_json_path: str,
    model_size: str = "tiny",
) -> dict:
    """Transcribes 16kHz WAV audio with word-level timestamps and saves a checkpoint JSON."""
    out_file = Path(output_json_path)
    if out_file.exists():
        return json.loads(out_file.read_text(encoding="utf-8"))

    audio_array = _load_wav_float32(wav_path)
    model = get_whisper_model(model_size=model_size)
    segments_iter, info = model.transcribe(
        audio_array,
        beam_size=5,
        word_timestamps=True,
        vad_filter=True,language="en")

    segments = []
    full_text_parts = []
    for seg in segments_iter:
        words = [
            {
                "word": w.word.strip(),
                "start": round(w.start, 3),
                "end": round(w.end, 3),
            }
            for w in (seg.words or [])
        ]
        text = seg.text.strip()
        if text:
            full_text_parts.append(text)
            segments.append(
                {
                    "id": seg.id,
                    "start": round(seg.start, 3),
                    "end": round(seg.end, 3),
                    "text": text,
                    "words": words,
                }
            )

    payload = {
        "language": info.language,
        "language_probability": round(info.language_probability, 3),
        "full_text": " ".join(full_text_parts),
        "segment_count": len(segments),
        "segments": segments,
    }

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload
