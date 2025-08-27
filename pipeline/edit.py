from pathlib import Path
from typing import Dict, Any, List
import os
from .utils import run_ffmpeg, srt_escape, media_info
from .antidetect import build_antidetect_filters
from .pro_enhance import enhance_postprocess


def render_clip(input_path: Path, srt_path: Path, out_path: Path, clip: Dict[str, Any], cfg: Dict[str, Any]):
    """
    Рендер клипа:
      - Вертикализация: горизонтальные кадры заполняем под 9:16 (fill), вертикальные — аккуратно pad.
      - Субтитры: компактнее и читабельнее (ASS-стиль с полупрозрачным фоном).
      - Красивый монтаж: лёгкий fade-in/out по видео и аудио.
      - Антидетект: сохраняем существующие фильтры из antidetect + atempo там же.
      - Постпроцесс: enhance_postprocess (музыка, дакинг, нормализация, вариация скорости).
    """
    base_fs = 30
    clip_len = max(0.1, clip["end"] - clip["start"])
    anti = build_antidetect_filters(
        seed_key=f"{input_path.name}:{clip['start']:.3f}-{clip['end']:.3f}",
        cfg=cfg,
        clip_duration=clip_len,
        base_sub_fontsize=base_fs
    )

    # Определяем ориентацию источника
    try:
        info = media_info(input_path)
        src_w, src_h = info.get("width", 1920), info.get("height", 1080)
    except Exception:
        src_w, src_h = 1920, 1080

    target_w, target_h = 1080, 1920
    target_ar = target_w / target_h
    src_ar = (src_w / src_h) if src_h else 16/9

    vf_chain: List[str] = []

    if src_ar >= target_ar:
        # ГОРИЗОНТАЛЬНЫЕ/ШИРОКИЕ → приближаем (fill): увеличиваем и кропаем по центру под 9:16
        vf_chain += [
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase",
            f"crop={target_w}:{target_h}:(iw-{target_w})/2:(ih-{target_h})/2"
        ]
    else:
        # ВЕРТИКАЛЬНЫЕ/УЗКИЕ → вписываем и дополняем полями (pad)
        vf_chain += [
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease",
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2"
        ]

    # Антидетект-видео (лёгкий шарп/виньетка/eq/шум — уже генерятся в anti['vf'])
    for f in anti.get("vf", []):
        vf_chain.append(f)

    # Субтитры — меньше и аккуратнее (центр снизу, полупрозрачный фон)
    # Если хочешь ещё меньше — поставь Fontsize=24.
    sub_fs = max(20, min(28, anti.get("subtitle_fontsize", 26)))
    style = (
        f"Fontname=DejaVu Sans,Fontsize={sub_fs},"
        f"PrimaryColour=&H00FFFFFF,OutlineColour=&H66000000,"
        f"BorderStyle=3,Outline=0,BackColour=&H66000000,Shadow=0,"
        f"Alignment=2,MarginV=100,MarginL=40,MarginR=40"
    )
    vf_chain.append(f"subtitles={srt_escape(srt_path)}:force_style='{style}'")

    # Оверлеи из anti (watermark/banner) — только если реально есть файл (см. правку в antidetect.py)
    ov = anti.get("overlay") or {}
    if ov.get("filter"):
        vf_chain.append(ov["filter"])

    # Лёгкий fade для видео
    fin = 0.25
    fout = 0.25
    if clip_len > (fin + fout + 0.1):
        vf_chain.append(f"fade=t=in:st=0:d={fin}")
        vf_chain.append(f"fade=t=out:st={max(0.0, clip_len - fout)}:d={fout}")

    # Аудио-цепочка: берём из anti['af'] (там atempo) + мягкий fade-in/out
    af_chain: List[str] = list(anti.get("af", []))
    if clip_len > (fin + fout + 0.1):
        af_chain.append(f"afade=t=in:st=0:d={fin}")
        af_chain.append(f"afade=t=out:st={max(0.0, clip_len - fout)}:d={fout}")

    final_vf = ",".join(vf_chain)

    ss = max(0.0, clip["start"])
    to = clip_len

    # Сначала рендерим «чистый» клип в temp, затем — постпроцесс (музыка/дакинг/нормализация/вариация скорости)
    tmp_out = out_path.with_suffix(".prepro.mp4")
    args = [
        "-ss", f"{ss:.3f}", "-i", str(input_path),
        "-t", f"{to:.3f}",
        "-filter_complex", final_vf,
        "-r", str(cfg["processing"]["target_fps"]),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
    ]

    # Если в исходнике есть звук — добавляем аудио фильтры; иначе — видео-only (звук добавится на постпроцессе)
    try:
        has_audio = media_info(input_path).get("has_audio", True)
    except Exception:
        has_audio = True

    if has_audio and af_chain:
        args += ["-c:a", "aac", "-b:a", "160k", "-af", ",".join(af_chain)]
    else:
        args += ["-an"]

    args += [str(tmp_out)]
    run_ffmpeg(args)

    # Постпроцесс (музыка, дакинг, нормализация, вариация скорости, вертикализация при необходимости)
    try:
        enhance_postprocess(tmp_out, out_path, cfg)
    finally:
        try:
            os.remove(tmp_out)
        except Exception:
            pass


