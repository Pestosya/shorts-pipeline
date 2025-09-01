from pathlib import Path
from typing import Dict, Any, List
import os, logging

from .utils import run_ffmpeg, srt_escape, media_info
from .antidetect import build_antidetect_filters
from .pro_enhance import enhance_postprocess
from .framing import suggest_crop

LOG = logging.getLogger("pipeline.edit")


def _build_sub_style(cfg: dict) -> str:
    sub = cfg.get("subtitles", {})
    font = sub.get("font", "Arial")
    size = int(sub.get("size", 22))
    margin_v = int(sub.get("margin_v", 120))
    margin_lr = int(sub.get("margin_lr", 180))   # чуть шире поля, чтобы столбец был уже
    line_spacing = int(sub.get("line_spacing", 2))

    # Жёсткий низ кадра, центр
    align = 2  # bottom-center

    style = str(sub.get("style", "outline")).lower()
    if style == "box":
        return (
            f"Fontname={font},Fontsize={size},Bold=0,"
            f"PrimaryColour=&H00FFFFFF,BackColour=&H44000000,"
            f"BorderStyle=3,Outline=0,Shadow=0,"
            f"Alignment={align},MarginV={margin_v},MarginL={margin_lr},MarginR={margin_lr},"
            f"WrapStyle=2,LineSpacing={line_spacing}"
        )
    elif style == "shadow":
        return (
            f"Fontname={font},Fontsize={size},Bold=0,"
            f"PrimaryColour=&H00FFFFFF,"
            f"BorderStyle=1,Outline=1,Shadow=3,"
            f"Alignment={align},MarginV={margin_v},MarginL={margin_lr},MarginR={margin_lr},"
            f"WrapStyle=2,LineSpacing={line_spacing}"
        )
    else:  # outline (легче всего читается)
        return (
            f"Fontname={font},Fontsize={size},Bold=0,"
            f"PrimaryColour=&H00FFFFFF,OutlineColour=&H99000000,"
            f"BorderStyle=1,Outline=2,Shadow=0,"
            f"Alignment={align},MarginV={margin_v},MarginL={margin_lr},MarginR={margin_lr},"
            f"WrapStyle=2,LineSpacing={line_spacing}"
        )




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

    # Геометрия исходника → выбираем fill/pad, c optional smart-crop
    # Геометрия исходника → выбираем fill/pad, c optional smart-crop
    try:
        info = media_info(input_path)
        src_w, src_h = int(info.get("width", 1920)), int(info.get("height", 1080))
        has_audio = bool(info.get("has_audio", True))
    except Exception:
        src_w, src_h, has_audio = 1920, 1080, True

    target_w = int(render.get("target_width", 1080))
    target_h = int(render.get("target_height", 1920))
    target_ar = target_w / target_h
    src_ar = (src_w / max(1, src_h)) if src_h else (16 / 9)

    vf_chain: List[str] = []
    smart_crop = bool(render.get("smart_crop", True))
    if src_ar >= target_ar:
        # landscape → fill + (опционально) умный кроп (лицо/интерес)
        if smart_crop:
            try:
                cx, cy = suggest_crop(input_path, clip["start"], clip_len, src_w, src_h, target_w, target_h)
                vf_chain += [
                    f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase",
                    f"crop={target_w}:{target_h}:{int(cx)}:{int(cy)}"
                ]
            except Exception as e:
                LOG.warning("smart_crop failed: %s; fallback to center-crop", e)
                vf_chain += [
                    f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase",
                    f"crop={target_w}:{target_h}:(iw-{target_w})/2:(ih-{target_h})/2"
                ]
        else:
            vf_chain += [
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase",
                f"crop={target_w}:{target_h}:(iw-{target_w})/2:(ih-{target_h})/2"
            ]
    else:
        # узкие/вертикальные → вписать и допаддить
        vf_chain += [
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease",
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2"
        ]

    # Антидетект (видео)
    for f in anti.get("vf", []):
        vf_chain.append(f)


    # Оверлеи из anti (watermark/banner) — уже безопасны после правки antidetect.py
    ov = anti.get("overlay") or {}
    if ov.get("filter"):
        vf_chain.append(ov["filter"])
    # Субтитры — компактнее и читабельнее
    # уменьшили относительно anti['subtitle_fontsize'] и добавили полупрозрачный фон
    # Субтитры — компактные и читабельные (Windows-дружелюбный Arial)
    # Субтитры — компактные: меньше кегль, большие поля, мягкий оутлайн

    style = _build_sub_style(cfg)
    vf_chain.append(f"subtitles=f='{srt_escape(srt_path)}':charenc=UTF-8:force_style='{style}'")

    # Плавные видео-фейды
    if clip_len > (fade_in + fade_out + 0.1):
        vf_chain.append(f"fade=t=in:st=0:d={fade_in}")
        vf_chain.append(f"fade=t=out:st={max(0.0, clip_len - fade_out)}:d={fade_out}")

    # Аудио-цепочка (антидетект + фейды), если есть звук у источника
    af_chain: List[str] = list(anti.get("af", []))
    # убираем любые изменения темпа на этой стадии — иначе сабы уедут
    af_chain = [f for f in af_chain if not f.strip().startswith("atempo=")]
    if has_audio and clip_len > (fade_in + fade_out + 0.1):
        af_chain.append(f"afade=t=in:st=0:d={fade_in}")
        af_chain.append(f"afade=t=out:st={max(0.0, clip_len - fade_out)}:d={fade_out}")

    final_vf = ",".join(vf_chain)
    ss = max(0.0, clip["start"])
    to = clip_len

    # Сначала рендерим «чистый» клип во временный файл…
    # Сначала рендерим «чистый» клип во временный файл…
    tmp_out = out_path.with_suffix(".prepro.mp4")
    args = [
        "-i", str(input_path),  # ← сначала вход
        "-ss", f"{ss:.3f}",  # ← точный seek ПОСЛЕ -i
        "-t", f"{to:.3f}",
        "-filter_complex", final_vf,
        "-r", str(cfg["processing"]["target_fps"]),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-avoid_negative_ts", "make_zero",
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
