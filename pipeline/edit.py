import os
from pathlib import Path
from typing import Dict, Any, List
from .utils import run_ffmpeg, srt_escape
from .antidetect import build_antidetect_filters
from .pro_enhance import enhance_postprocess

def render_clip(input_path: Path, srt_path: Path, out_path: Path, clip: Dict[str, Any], cfg: Dict[str, Any]):
    base_fs = 30
    clip_len = max(0.1, clip["end"] - clip["start"])
    anti = build_antidetect_filters(seed_key=f"{input_path.name}-{clip['start']:.3f}-{clip['end']:.3f}", cfg=cfg, clip_duration=clip_len, base_sub_fontsize=base_fs)

    vf_chain: List[str] = [
        "scale=1080:1920:force_original_aspect_ratio=decrease",
        "pad=1080:1920:(1080-iw)/2:(1920-ih)/2"
    ]

    if srt_path.exists():
        vf_chain.append(f"subtitles='{srt_escape(srt_path)}':force_style='Outline=1,Fontsize={anti['subtitle_fontsize']}'")

    banner = anti["overlay"]
    if banner["type"] == "banner" and banner["image"]:
        vf = (
            f"[0:v] {','.join(vf_chain)} [base]; "
            f"movie='{banner['image']}' [banner]; "
        )
        if anti["vf"]:
            vf = f"[base] {','.join(anti['vf'])} [base2]; " + vf
            base_in = "base2"
        else:
            base_in = "base"
        vf = vf + banner["filter"].replace("[base]", f"[{base_in}]")
        final_vf = vf
    else:
        vf_chain = vf_chain + anti["vf"]
        if banner["type"] == "watermark" and banner["image"]:
            x_y = cfg["branding"].get("watermark_pos") or "10:10"
            vf = "[0:v] " + ",".join([p for p in vf_chain if not p.startswith("movie=")]) + " [v1]; movie='" + banner["image"] + "' [wm]; [v1][wm] overlay=" + x_y
            final_vf = vf
        else:
            final_vf = ",".join(vf_chain)

    af_chain = [f"loudnorm=I={cfg['processing']['audio_lufs']}:LRA=11:TP=-1.5"] + anti["af"]

    ss = max(0.0, clip["start"])
    to = clip_len

    tmp_out = out_path.with_suffix(".prepro.mp4")

    args = [
        "-ss", f"{ss:.3f}", "-i", str(input_path),
        "-t", f"{to:.3f}",
        "-filter_complex", final_vf,
        "-r", str(cfg["processing"]["target_fps"]),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k",
        "-af", ",".join(af_chain),
        str(tmp_out)
    ]
    run_ffmpeg(args)

    # Пост-обработка: вертикальные режимы, музыка, дакинг, нормализация речи, speed-вар.
    try:
        enhance_postprocess(tmp_out, out_path, cfg)
    finally:
        try:
            os.remove(tmp_out)
        except Exception:
            pass

