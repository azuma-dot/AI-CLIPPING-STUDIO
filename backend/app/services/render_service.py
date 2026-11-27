import os
import subprocess
from pathlib import Path
import cv2


def find_capcut_exe() -> str | None:
    """Finds local CapCut Desktop executable on Windows if installed."""
    candidates: list[Path] = []
    for env_key in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_key, "")
        if base and Path(base).exists():
            candidates.extend(Path(base).glob("CapCut/**/CapCut.exe"))
    valid = [c for c in candidates if c.is_file()]
    if not valid:
        return None
    valid.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(valid[0])


def launch_in_capcut(video_path: str) -> dict:
    """Opens a 9:16 Short video in local CapCut Desktop (or reveals it in Explorer if CapCut isn't found)."""
    vp = Path(video_path)
    if not vp.exists():
        return {"ok": False, "capcut_found": False, "message": f"Clip file not found: {video_path}"}

    capcut_exe = find_capcut_exe()
    if capcut_exe:
        subprocess.Popen([capcut_exe, str(vp)], close_fds=True)
        return {
            "ok": True,
            "capcut_found": True,
            "capcut_exe": capcut_exe,
            "message": f"Opened Short in CapCut Desktop ({vp.name}).",
        }

    subprocess.Popen(["explorer.exe", "/select,", str(vp)], close_fds=True)
    return {
        "ok": True,
        "capcut_found": False,
        "message": f"CapCut.exe not found in default AppData path ? opened Explorer with {vp.name} selected.",
    }


def _format_ass_time(seconds: float) -> str:
    sec = max(0.0, seconds)
    hours = int(sec // 3600)
    minutes = int((sec % 3600) // 60)
    secs = int(sec % 60)
    centis = int(round((sec - int(sec)) * 100))
    if centis >= 100:
        secs += 1
        centis = 0
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _format_srt_time(seconds: float) -> str:
    sec = max(0.0, seconds)
    hours = int(sec // 3600)
    minutes = int((sec % 3600) // 60)
    secs = int(sec % 60)
    millis = int(round((sec - int(sec)) * 1000))
    if millis >= 1000:
        secs += 1
        millis = 0
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def compute_speaker_crop_expression(
    video_path: str,
    clip_start: float,
    clip_end: float,
) -> tuple[str, bool]:
    """
    Samples frames with OpenCV Haar face detection and builds a balanced binary-tree
    piecewise-linear normalized X expression [0..1] centered on the active speaker.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return "0.5000", False

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    duration = max(1.0, clip_end - clip_start)
    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    step_sec = 1.5
    sample_times = []
    t = 0.0
    while t <= duration:
        sample_times.append(round(t, 2))
        t += step_sec
    if not sample_times or sample_times[-1] < duration:
        sample_times.append(round(duration, 2))

    raw_positions: list[tuple[float, float | None]] = []
    for rel_t in sample_times:
        abs_t = clip_start + rel_t
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(abs_t * fps))
        ok, frame = cap.read()
        if not ok or frame is None:
            raw_positions.append((rel_t, None))
            continue

        h, w = frame.shape[:2]
        scale = 640.0 / max(w, 1)
        small = cv2.resize(frame, (int(w * scale), int(h * scale)))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        faces = cascade.detectMultiScale(
            gray,
            scaleFactor=1.15,
            minNeighbors=5,
            minSize=(36, 36),
        )
        if len(faces) > 0:
            fx, fy, fw, fh = max(faces, key=lambda b: b[2] * b[3])
            center_norm = (fx + fw / 2.0) / max(small.shape[1], 1)
            raw_positions.append((rel_t, max(0.15, min(0.85, float(center_norm)))))
        else:
            raw_positions.append((rel_t, None))

    cap.release()

    detected = [p for p in raw_positions if p[1] is not None]
    if not detected:
        return "0.5000", False

    first_known = detected[0][1]
    filled: list[tuple[float, float]] = []
    last_val = first_known
    for rel_t, val in raw_positions:
        if val is not None:
            last_val = val
        filled.append((rel_t, float(last_val)))

    smoothed: list[tuple[float, float]] = []
    for i, (rel_t, _) in enumerate(filled):
        window = [filled[j][1] for j in range(max(0, i - 1), min(len(filled), i + 2))]
        smoothed.append((rel_t, sum(window) / len(window)))

    simplified: list[tuple[float, float]] = [smoothed[0]]
    for pt in smoothed[1:-1]:
        if abs(pt[1] - simplified[-1][1]) >= 0.012:
            simplified.append(pt)
    if len(smoothed) > 1:
        simplified.append(smoothed[-1])

    if len(simplified) > 32:
        stride = (len(simplified) - 1) / 31.0
        simplified = [simplified[round(i * stride)] for i in range(32)]

    if len(simplified) == 1:
        norm_expr = f"{simplified[0][1]:.4f}"
    else:
        n_segs = len(simplified) - 1

        def _build_tree(lo: int, hi: int) -> str:
            if lo == hi:
                t0, x0 = simplified[lo]
                t1, x1 = simplified[lo + 1]
                dt = max(0.05, t1 - t0)
                slope = (x1 - x0) / dt
                seg = f"{x0:.4f}+({slope:.5f})*(t-{t0:.2f})"
                if lo == n_segs - 1:
                    return f"if(lt(t,{t1:.2f}),{seg},{x1:.4f})"
                return seg
            mid = (lo + hi) // 2
            t_split = simplified[mid + 1][0]
            return f"if(lt(t,{t_split:.2f}),{_build_tree(lo, mid)},{_build_tree(mid + 1, hi)})"

        norm_expr = _build_tree(0, n_segs - 1)

    return norm_expr.replace(",", "\\,"), True


def build_srt_subtitles(
    transcript: dict,
    clip_start: float,
    clip_end: float,
    srt_output_path: str,
    words_per_chunk: int = 4,
) -> str:
    """Exports standard .srt subtitles alongside clips for CapCut Desktop editing."""
    duration = max(1.0, clip_end - clip_start)
    words_in_clip = []
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []):
            w_start = float(w["start"])
            w_end = float(w["end"])
            if w_end >= clip_start and w_start <= clip_end:
                words_in_clip.append(
                    {
                        "word": w["word"].strip().upper(),
                        "start": max(0.0, w_start - clip_start),
                        "end": max(0.05, min(duration, w_end - clip_start)),
                    }
                )

    blocks = []
    cue_idx = 1
    for i in range(0, len(words_in_clip), words_per_chunk):
        chunk = words_in_clip[i : i + words_per_chunk]
        start_t = chunk[0]["start"]
        end_t = max(start_t + 0.25, chunk[-1]["end"] + 0.1)
        text = " ".join(item["word"] for item in chunk)
        blocks.append(f"{cue_idx}\n{_format_srt_time(start_t)} --> {_format_srt_time(end_t)}\n{text}\n")
        cue_idx += 1

    out_path = Path(srt_output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(blocks), encoding="utf-8")
    return str(out_path)


def build_animated_ass_subtitles(
    transcript: dict,
    clip_start: float,
    clip_end: float,
    hook_caption: str,
    ass_output_path: str,
    words_per_chunk: int = 3,
    play_res_x: int = 1080,
    play_res_y: int = 1920,
) -> str:
    """Builds an .ass subtitle file with top hook banner + yellow karaoke captions."""
    duration = max(1.0, clip_end - clip_start)
    words_in_clip = []
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []):
            w_start = float(w["start"])
            w_end = float(w["end"])
            if w_end >= clip_start and w_start <= clip_end:
                words_in_clip.append(
                    {
                        "word": w["word"].strip().upper(),
                        "start": max(0.0, w_start - clip_start),
                        "end": max(0.05, min(duration, w_end - clip_start)),
                    }
                )

    is_wide = play_res_x > play_res_y
    cap_size = 58 if is_wide else 74
    hook_size = 46 if is_wide else 62
    cap_margin_v = 95 if is_wide else 360
    hook_margin_v = 55 if is_wide else 150

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial Black,{cap_size},&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,3,2,60,60,{cap_margin_v},1
Style: HookBanner,Arial Black,{hook_size},&H0000FFFF,&H00FFFFFF,&H00000000,&HC8000000,-1,0,0,0,100,100,1,0,3,16,0,8,70,70,{hook_margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    safe_hook = hook_caption.strip().upper().replace("{", "").replace("}", "")
    dialogue_lines = []
    if safe_hook:
        dialogue_lines.append(
            f"Dialogue: 1,0:00:00.00,{_format_ass_time(duration)},HookBanner,,0,0,0,,{safe_hook}"
        )

    for i in range(0, len(words_in_clip), words_per_chunk):
        chunk = words_in_clip[i : i + words_per_chunk]
        for active_idx, active_word in enumerate(chunk):
            line_start = active_word["start"]
            if active_idx + 1 < len(chunk):
                line_end = chunk[active_idx + 1]["start"]
            else:
                line_end = active_word["end"] + 0.12

            if line_end <= line_start:
                line_end = line_start + 0.08

            styled_tokens = []
            for idx, item in enumerate(chunk):
                safe_word = item["word"].replace("{", "").replace("}", "")
                if idx == active_idx:
                    styled_tokens.append(
                        r"{\c&H0000FFFF&\fscx112\fscy112}"
                        + safe_word
                        + r"{\c&H00FFFFFF&\fscx100\fscy100}"
                    )
                else:
                    styled_tokens.append(safe_word)

            text_line = " ".join(styled_tokens)
            dialogue_lines.append(
                f"Dialogue: 0,{_format_ass_time(line_start)},{_format_ass_time(line_end)},Default,,0,0,0,,{text_line}"
            )

    out_path = Path(ass_output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(header + "\n".join(dialogue_lines) + "\n", encoding="utf-8")
    return str(out_path)


def render_vertical_clip(
    source_video_path: str,
    transcript: dict,
    clip: dict,
    project_id: str,
    temp_dir: str,
    output_dir: str,
    aspect_ratio: str = "9:16",
    captions_enabled: bool = True,
) -> dict:
    """
    Renders clip in 9:16 (Shorts with speaker tracking + CapCut punch-zooms + clean CapCut master)
    or 16:9 widescreen, with optional animated karaoke captions and progress bar.
    """
    idx = clip["clip_index"]
    start = float(clip["start"])
    end = float(clip["end"])
    duration = max(1.0, round(end - start, 3))
    ratio = "16:9" if str(aspect_ratio).strip() == "16:9" else "9:16"
    is_short = ratio == "9:16"

    temp_path = Path(temp_dir)
    out_path = Path(output_dir) / project_id
    temp_path.mkdir(parents=True, exist_ok=True)
    out_path.mkdir(parents=True, exist_ok=True)

    out_w, out_h = (1080, 1920) if is_short else (1920, 1080)
    bar_y = out_h - 14

    ass_filename = f"{project_id}_clip_{idx}.ass"
    ass_full_path = temp_path / ass_filename
    srt_full_path = out_path / f"clip_{idx}_{project_id}.srt"

    build_srt_subtitles(
        transcript=transcript,
        clip_start=start,
        clip_end=end,
        srt_output_path=str(srt_full_path),
    )

    if captions_enabled:
        build_animated_ass_subtitles(
            transcript=transcript,
            clip_start=start,
            clip_end=end,
            hook_caption=clip.get("hook_caption", clip.get("title", "VIRAL MOMENT")),
            ass_output_path=str(ass_full_path),
            play_res_x=out_w,
            play_res_y=out_h,
        )

    mp4_filename = f"clip_{idx}_{project_id}.mp4"
    mp4_full_path = out_path / mp4_filename
    capcut_clean_path = out_path / f"short_clean_{idx}_{project_id}.mp4"

    face_tracked = False
    if is_short:
        norm_x_expr, face_tracked = compute_speaker_crop_expression(
            video_path=source_video_path,
            clip_start=start,
            clip_end=end,
        )
        # CapCut Pro style rhythmic punch-zoom (1.0x <-> 1.08x every 4.5s) + speaker tracking
        zoom_cond = r"lt(mod(t\,9.0)\,4.5)"
        cw = rf"if({zoom_cond}\,1166\,1080)"
        ch = rf"if({zoom_cond}\,2074\,1920)"
        cx = rf"min(max((iw-{cw})*({norm_x_expr})\,0)\,iw-{cw})"
        cy = rf"if({zoom_cond}\,0\,76)"
        base_video_chain = (
            f"[0:v]scale=-2:2074,"
            f"crop=w='{cw}':h='{ch}':x='{cx}':y='{cy}',"
            f"scale=1080:1920"
        )
    else:
        base_video_chain = (
            "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2"
        )

    if captions_enabled:
        filter_complex = (
            f"{base_video_chain},ass={ass_filename}[vmain];"
            f"color=c=0x1e293b@0.8:s={out_w}x14:d={duration}[bgbar];"
            f"color=c=0xfacc15:s={out_w}x14:d={duration}[fgbar];"
            f"[vmain][bgbar]overlay=0:{bar_y}:shortest=1[vbg];"
            f"[vbg][fgbar]overlay=x='-{out_w}+({out_w}*t/{duration})':y={bar_y}:shortest=1[vout]"
        )
    else:
        filter_complex = f"{base_video_chain}[vout]"

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start),
        "-t",
        str(duration),
        "-i",
        source_video_path,
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "21",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(mp4_full_path),
    ]
    subprocess.run(cmd, cwd=str(temp_path), check=True, capture_output=True, text=True)

    # For 9:16 Shorts, also keep a clean caption-free master for CapCut Pro auto-editing
    if is_short:
        if captions_enabled:
            clean_cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                str(start),
                "-t",
                str(duration),
                "-i",
                source_video_path,
                "-filter_complex",
                f"{base_video_chain}[vclean]",
                "-map",
                "[vclean]",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(capcut_clean_path),
            ]
            subprocess.run(clean_cmd, cwd=str(temp_path), check=True, capture_output=True, text=True)
        else:
            capcut_clean_path = mp4_full_path

    return {
        **clip,
        "duration": duration,
        "aspect_ratio": ratio,
        "is_short": is_short,
        "captions_enabled": bool(captions_enabled),
        "face_tracked": face_tracked,
        "output_path": str(mp4_full_path),
        "capcut_source_path": str(capcut_clean_path) if is_short else "",
        "srt_path": str(srt_full_path),
        "subtitle_path": str(ass_full_path) if captions_enabled else "",
    }

def pro_polish_clip(
    source_video_path: str,
    transcript: dict,
    clip: dict,
    project_id: str,
    temp_dir: str,
    output_dir: str,
    preset: str = "punch_glow",
    zoom_scale: float = 1.08,
    captions_enabled: bool = True,
    normalize_audio: bool = True,
    layout_mode: str = "auto",
    swap_regions: bool = False,
    secondary_size: float = 0.35,
    cam_x: float = 0.82,
    cam_y: float = 0.18,
    game_x: float = 0.50,
    game_y: float = 0.50,
) -> dict:
    """
    Re-renders a single clip with Studio Pro presets, streamer/gameplay split or PiP layouts,
    punch-zoom intensity, color/contrast polish, optional captions, and loudness normalization.
    """
    idx = int(clip["clip_index"])
    start = float(clip["start"])
    end = float(clip["end"])
    duration = max(1.0, round(end - start, 3))
    ratio = "16:9" if str(clip.get("aspect_ratio", "9:16")).strip() == "16:9" else "9:16"
    is_short = ratio == "9:16"

    temp_path = Path(temp_dir)
    out_path = Path(output_dir) / project_id
    temp_path.mkdir(parents=True, exist_ok=True)
    out_path.mkdir(parents=True, exist_ok=True)

    out_w, out_h = (1080, 1920) if is_short else (1920, 1080)
    bar_y = out_h - 14

    ass_filename = f"{project_id}_pro_{idx}.ass"
    ass_full_path = temp_path / ass_filename
    if captions_enabled:
        build_animated_ass_subtitles(
            transcript=transcript,
            clip_start=start,
            clip_end=end,
            hook_caption=clip.get("hook_caption", clip.get("title", "VIRAL MOMENT")),
            ass_output_path=str(ass_full_path),
            play_res_x=out_w,
            play_res_y=out_h,
        )

    z = max(1.0, min(1.18, float(zoom_scale)))
    sec_ratio = max(0.15, min(0.65, float(secondary_size)))
    cx_pos = max(0.0, min(1.0, float(cam_x)))
    cy_pos = max(0.0, min(1.0, float(cam_y)))
    gx_pos = max(0.0, min(1.0, float(game_x)))
    gy_pos = max(0.0, min(1.0, float(game_y)))
    l_mode = (layout_mode or "auto").strip().lower()
    if swap_regions:
        cx_pos, gx_pos = gx_pos, cx_pos
        cy_pos, gy_pos = gy_pos, cy_pos

    face_tracked = False
    use_split_916 = is_short and l_mode in ("split_916", "cam_top", "cam_bottom", "game_top", "game_bottom")
    use_pip_169 = (not is_short) and l_mode in ("pip_169", "split_169", "cam_game_169")

    if use_split_916:
        cam_is_top = l_mode not in ("cam_bottom", "game_top")
        if swap_regions:
            cam_is_top = not cam_is_top

        top_h = int(round(1920 * sec_ratio))
        if top_h % 2:
            top_h += 1
        top_h = max(240, min(1680, top_h))
        bot_h = 1920 - top_h
        if bot_h % 2:
            bot_h -= 1

        cam_h_out = top_h if cam_is_top else bot_h
        game_h_out = bot_h if cam_is_top else top_h
        cam_y_out = 0 if cam_is_top else top_h
        game_y_out = top_h if cam_is_top else 0

        cam_crop_w = min(1920, max(2, int(round(1080 * 1.15))))
        cam_crop_h = max(2, int(round(cam_h_out * 1.15)))
        game_crop_w = min(1920, max(2, int(round(1080 * 1.15))))
        game_crop_h = max(2, int(round(game_h_out * 1.15)))

        v_chain = (
            f"[0:v]split=2[vsrc1][vsrc2];"
            f"[vsrc1]scale=-2:{cam_crop_h},crop=w={cam_crop_w}:h={cam_crop_h}:x='(iw-{cam_crop_w})*{cx_pos}':y='(ih-{cam_crop_h})*{cy_pos}',scale=1080:{cam_h_out}[cam_pane];"
            f"[vsrc2]scale=-2:{game_crop_h},crop=w={game_crop_w}:h={game_crop_h}:x='(iw-{game_crop_w})*{gx_pos}':y='(ih-{game_crop_h})*{gy_pos}',scale=1080:{game_h_out}[game_pane];"
            f"color=c=black:s=1080x1920:d={duration}[canvas];"
            f"[canvas][cam_pane]overlay=0:{cam_y_out}:shortest=1[c1];"
            f"[c1][game_pane]overlay=0:{game_y_out}:shortest=1[vgraded_src]"
        )
    elif use_pip_169:
        pip_w = int(round(1920 * sec_ratio))
        if pip_w % 2:
            pip_w += 1
        pip_w = max(240, min(960, pip_w))
        pip_h = int(round(pip_w * 9 / 16))
        if pip_h % 2:
            pip_h += 1
        pad_x = 32
        pad_y = 32
        pip_x = int(round((1920 - pip_w - pad_x * 2) * cx_pos)) + pad_x
        pip_y = int(round((1080 - pip_h - pad_y * 2) * cy_pos)) + pad_y
        sec_crop_w = min(1920, max(2, int(round(pip_w * 1.15))))
        sec_crop_h = max(2, int(round(pip_h * 1.15)))

        v_chain = (
            f"[0:v]split=2[base16][pip16];"
            f"[base16]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2[main16];"
            f"[pip16]scale=-2:{sec_crop_h},crop=w={sec_crop_w}:h={sec_crop_h}:x='(iw-{sec_crop_w})*{gx_pos}':y='(ih-{sec_crop_h})*{gy_pos}',scale={pip_w}:{pip_h}[pip_box];"
            f"[main16][pip_box]overlay={pip_x}:{pip_y}:shortest=1[vgraded_src]"
        )
    elif is_short and z > 1.005:
        norm_x_expr, face_tracked = compute_speaker_crop_expression(
            video_path=source_video_path,
            clip_start=start,
            clip_end=end,
        )
        zoom_w = int(round(1080 * z))
        zoom_h = int(round(1920 * z))
        if zoom_w % 2:
            zoom_w += 1
        if zoom_h % 2:
            zoom_h += 1
        y_hi = max(0, zoom_h - 1920)
        y_lo = y_hi // 2
        zoom_cond = r"lt(mod(t\,8.0)\,4.0)"
        cw = rf"if({zoom_cond}\,{zoom_w}\,1080)"
        ch = rf"if({zoom_cond}\,{zoom_h}\,1920)"
        cx = rf"min(max((iw-{cw})*({norm_x_expr})\,0)\,iw-{cw})"
        cy = rf"if({zoom_cond}\,0\,{y_lo})"
        v_chain = (
            f"[0:v]scale=-2:{zoom_h},"
            f"crop=w='{cw}':h='{ch}':x='{cx}':y='{cy}',"
            f"scale=1080:1920[vgraded_src]"
        )
    elif is_short:
        norm_x_expr, face_tracked = compute_speaker_crop_expression(
            video_path=source_video_path,
            clip_start=start,
            clip_end=end,
        )
        v_chain = f"[0:v]scale=-2:1920,crop=1080:1920:x='(iw-1080)*({norm_x_expr})':y=0[vgraded_src]"
    else:
        face_tracked = False
        v_chain = (
            "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2[vgraded_src]"
        )

    p_key = (preset or "punch_glow").strip().lower()
    if p_key == "cinema_pop":
        grade_chain = "[vgraded_src]eq=contrast=1.08:brightness=0.02:saturation=1.18[vgraded]"
    elif p_key == "clean_master":
        captions_enabled = False
        grade_chain = "[vgraded_src]null[vgraded]"
    else:
        grade_chain = "[vgraded_src]eq=contrast=1.05:brightness=0.01:saturation=1.12[vgraded]"

    if captions_enabled:
        filter_complex = (
            f"{v_chain};{grade_chain};[vgraded]ass={ass_filename}[vmain];"
            f"color=c=0x1e293b@0.8:s={out_w}x14:d={duration}[bgbar];"
            f"color=c=0xfacc15:s={out_w}x14:d={duration}[fgbar];"
            f"[vmain][bgbar]overlay=0:{bar_y}:shortest=1[vbg];"
            f"[vbg][fgbar]overlay=x='-{out_w}+({out_w}*t/{duration})':y={bar_y}:shortest=1[vout]"
        )
    else:
        filter_complex = f"{v_chain};{grade_chain};[vgraded]null[vout]"

    pro_filename = f"pro_clip_{idx}_{project_id}.mp4"
    pro_full_path = out_path / pro_filename

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start),
        "-t",
        str(duration),
        "-i",
        source_video_path,
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-map",
        "0:a?",
    ]
    if normalize_audio:
        cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(pro_full_path),
    ]
    subprocess.run(cmd, cwd=str(temp_path), check=True, capture_output=True, text=True)

    return {
        **clip,
        "duration": duration,
        "captions_enabled": bool(captions_enabled),
        "face_tracked": face_tracked,
        "pro_preset": p_key,
        "layout_mode": l_mode,
        "output_path": str(pro_full_path),
    }
