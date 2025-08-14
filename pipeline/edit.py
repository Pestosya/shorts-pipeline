from pathlib import Path
from typing import Dict, Any
from .utils import run_ffmpeg, srt_escape

def render_clip(input_path: Path, srt_path: Path, out_path: Path, clip: Dict[str, Any], cfg: Dict[str, Any]):
    vf = [
        # Вписать в 1080x1920 без искажений и добавить поля
        "scale=1080:1920:force_original_aspect_ratio=decrease",
        "pad=1080:1920:(1080-iw)/2:(1920-ih)/2"
    ]

    # Субтитры
    if srt_path.exists():
        vf.append(f"subtitles='{srt_escape(srt_path)}':force_style='Outline=1,Fontsize=30'")

    # Водяной знак
    wm = cfg["branding"].get("watermark") or ""
    if wm:
        x, y = (cfg["branding"].get("watermark_pos") or "10:10").split(":")
        vf.append(f"overlay={x}:{y}")

    af = [f"loudnorm=I={cfg['processing']['audio_lufs']}:LRA=11:TP=-1.5"]
    ss = max(0.0, clip["start"])
    to = max(0.1, clip["end"] - clip["start"])

    run_ffmpeg([
        "-ss", f"{ss:.3f}",
        "-i", str(input_path),
        "-t", f"{to:.3f}",
        "-vf", ",".join(vf),
        "-r", str(cfg["processing"]["target_fps"]),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "160k",
        "-af", ",".join(af),
        str(out_path)
    ])
