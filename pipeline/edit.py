import os
from pathlib import Path
from typing import Dict, Any
from .utils import run_ffmpeg, srt_escape
from .pro_enhance import enhance_postprocess
from .antidetect import build_antidetect_filters

def render_clip(input_path: Path, srt_path: Path, out_path: Path, clip: Dict[str, Any], cfg: Dict[str, Any]):
    """
    Рендер клипа:
      - Вертикализация: горизонтальные — fill (крупнее, без чёрных полей), вертикальные — аккуратный pad
      - Субтитры: компактные, с полупрозрачным фоном (ASS force_style)
      - Плавный fade-in/out по видео и звуку
      - Антидетект: сохраняем фильтры из antidetect.py (eq/noise/unsharp/и т.п.)
      - Постпроцесс: музыка/дакинг/нормализация/вариация скорости (enhance_postprocess)
    """
    # Конфиги рендера (безопасные дефолты, можно вынести в config.yaml → секция render)
    render = cfg.get("render", {})
    target_w = int(render.get("target_width", 1080))
    target_h = int(render.get("target_height", 1920))
    fade_in = float(render.get("fade_in_sec", 0.25))
    fade_out = float(render.get("fade_out_sec", 0.25))
    sub_base = int(render.get("subtitle_fontsize_base", 26))  # общий базовый, потом чуть уменьшим

    base_fs = 30
    clip_len = max(0.1, clip["end"] - clip["start"])

    # Антидетект фильтры (твои)
    anti = build_antidetect_filters(
        seed_key=f"{input_path.name}:{clip['start']:.3f}-{clip['end']:.3f}",
        cfg=cfg,
        clip_duration=clip_len,
        base_sub_fontsize=base_fs
    )

    # Геометрия исходника → выбираем fill/pad
    try:
        info = media_info(input_path)
        src_w, src_h = int(info.get("width", 1920)), int(info.get("height", 1080))
        has_audio = bool(info.get("has_audio", True))
    except Exception:
        src_w, src_h, has_audio = 1920, 1080, True

    target_ar = target_w / target_h
    src_ar = (src_w / src_h) if src_h else (16/9)

    vf_chain: List[str] = []
    if src_ar >= target_ar:
        # Широкие/горизонтальные → fill (увеличить и центр-кропнуть под 9:16)
        vf_chain += [
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase",
            f"crop={target_w}:{target_h}:(iw-{target_w})/2:(ih-{target_h})/2"
        ]
    else:
        # Узкие/вертикальные → вписать и допаддить
        vf_chain += [
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease",
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2"
        ]

    # Антидетект (видео)
    for f in anti.get("vf", []):
        vf_chain.append(f)

    # Субтитры — компактнее и читабельнее
    # уменьшили относительно anti['subtitle_fontsize'] и добавили полупрозрачный фон
    sub_fs = max(20, min(28, anti.get("subtitle_fontsize", sub_base) - 2))
    margin_v = int(render.get("subtitle_margin_v", 90))
    style = (
        f"Fontname=DejaVu Sans,Fontsize={sub_fs},"
        f"PrimaryColour=&H00FFFFFF,OutlineColour=&H66000000,"
        f"BorderStyle=3,Outline=0,BackColour=&H66000000,Shadow=0,"
        f"Alignment=2,MarginV={margin_v},MarginL=40,MarginR=40,WrapStyle=3"
    )
    vf_chain.append(f"subtitles=f='{srt_escape(srt_path)}':force_style='{style}'")

    # Оверлеи из anti (watermark/banner) — уже безопасны после правки antidetect.py
    ov = anti.get("overlay") or {}
    if ov.get("filter"):
        vf_chain.append(ov["filter"])

    # Плавные видео-фейды
    if clip_len > (fade_in + fade_out + 0.1):
        vf_chain.append(f"fade=t=in:st=0:d={fade_in}")
        vf_chain.append(f"fade=t=out:st={max(0.0, clip_len - fade_out)}:d={fade_out}")

    # Аудио-цепочка (антидетект + фейды), если есть звук у источника
    af_chain: List[str] = list(anti.get("af", []))
    if has_audio and clip_len > (fade_in + fade_out + 0.1):
        af_chain.append(f"afade=t=in:st=0:d={fade_in}")
        af_chain.append(f"afade=t=out:st={max(0.0, clip_len - fade_out)}:d={fade_out}")

    final_vf = ",".join(vf_chain)
    ss = max(0.0, clip["start"])
    to = clip_len

    # Сначала рендерим «чистый» клип во временный файл…
    tmp_out = out_path.with_suffix(".prepro.mp4")
    args = [
        "-ss", f"{ss:.3f}", "-i", str(input_path),
        "-t", f"{to:.3f}",
        "-filter_complex", final_vf,
        "-r", str(cfg["processing"]["target_fps"]),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
    ]
    if has_audio and af_chain:
        args += ["-c:a", "aac", "-b:a", "160k", "-af", ",".join(af_chain)]
    else:
        args += ["-an"]
    args += [str(tmp_out)]
    run_ffmpeg(args)

    # …затем финальный постпроцесс: музыка/дакинг/нормализация/вариация скорости
    try:
        enhance_postprocess(tmp_out, out_path, cfg)
    finally:
        try:
            os.remove(tmp_out)
        except Exception:
            pass
