from pathlib import Path
from typing import Dict, Any, List
import os, logging

from .utils import run_ffmpeg, srt_escape, media_info
from .antidetect import build_antidetect_filters
# pro_enhance можно оставить, если используешь постпроцесс. Если нет — закомментируй импорт и вызов.
from .pro_enhance import enhance_postprocess

LOG = logging.getLogger("pipeline.edit")

def _build_sub_style(cfg: dict) -> str:
    sub = cfg.get("subtitles", {})
    font = sub.get("font", "Arial")
    size = int(sub.get("size", 22))
    margin_v = int(sub.get("margin_v", 130))
    margin_lr = int(sub.get("margin_lr", 190))
    line_spacing = int(sub.get("line_spacing", 2))
    # Жёсткий низ кадра
    align = 2  # 2 = bottom-center

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
    else:  # outline
        return (
            f"Fontname={font},Fontsize={size},Bold=0,"
            f"PrimaryColour=&H00FFFFFF,OutlineColour=&H99000000,"
            f"BorderStyle=1,Outline=2,Shadow=0,"
            f"Alignment={align},MarginV={margin_v},MarginL={margin_lr},MarginR={margin_lr},"
            f"WrapStyle=2,LineSpacing={line_spacing}"
        )

def render_clip(input_path: Path, srt_path: Path, out_path: Path, clip: Dict[str, Any], cfg: Dict[str, Any]):
    """
    Рендер клипа с устойчивым графом:
    [0:v] геометрия → антифильтры → overlay? → format → subtitles → fade  => [vout]
    [0:a] afade (без смены темпа на этой стадии)                             [aout]
    - Явные -map [vout]/[aout]
    - Точный seek (-ss ПОСЛЕ -i)
    """
    render = cfg.get("render", {})
    target_w = int(render.get("target_width", 1080))
    target_h = int(render.get("target_height", 1920))
    fade_in = float(render.get("fade_in_sec", 0.25))
    fade_out = float(render.get("fade_out_sec", 0.25))

    ss = max(0.0, float(clip["start"]))
    to = max(0.1, float(clip["end"] - clip["start"]))

    # Инфа об исходнике
    try:
        info = media_info(input_path)
        src_w, src_h = int(info.get("width", 1920)), int(info.get("height", 1080))
        has_audio = bool(info.get("has_audio", True))
    except Exception:
        src_w, src_h, has_audio = 1920, 1080, True

    src_ar = src_w / max(1, src_h)
    tgt_ar = target_w / target_h

    # 1) Геометрия (всегда начинаем с [0:v])
    vf_nodes: List[str] = []
    if src_ar >= tgt_ar:
        # landscape → fill + центр-кроп
        vf_nodes.append(f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=increase[v1]")
        vf_nodes.append(f"[v1]crop={target_w}:{target_h}:(iw-{target_w})/2:(ih-{target_h})/2[v2]")
    else:
        # узкие/вертикальные → decrease + pad
        vf_nodes.append(f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease[v1]")
        vf_nodes.append(f"[v1]pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2[v2]")

    current = "[v2]"

    # 2) Антидетект (цвет/шарп/шум и т.д.) — фильтры без линков, цепляем на текущий
    anti = build_antidetect_filters(
        seed_key=f"{input_path.name}:{clip['start']:.3f}-{clip['end']:.3f}",
        cfg=cfg,
        clip_duration=to,
        base_sub_fontsize=30
    )
    # 2) Антидетект (цвет/шарп/шум) — без лишних запятых и с уникальными метками
    anti = build_antidetect_filters(
        seed_key=f"{input_path.name}:{clip['start']:.3f}-{clip['end']:.3f}",
        cfg=cfg,
        clip_duration=to,
        base_sub_fontsize=30
    )
    anti_vf: List[str] = [f.strip() for f in anti.get("vf", []) if f and f.strip()]

    label = current  # например, "[v2]" после геометрии
    step = 3
    for f in anti_vf:
        # ВАЖНО: НИКАКОЙ запятой после метки. Правильно: "[v2]noise=..."
        vf_nodes.append(f"{label}{f}[v{step}]")
        label = f"[v{step}]"
        step += 1

    current = label

    # 3) Overlay (если картинка существует) — ДЕЛАЕМ НАДЁЖНО через второй input
    ov = (anti.get("overlay") or {})
    overlay_img = ov.get("image")
    overlay_added = False
    if overlay_img and os.path.isfile(overlay_img):
        # масштабируем оверлей в safe area, сверху/снизу по cfg (по умолчанию — верх)
        pad = int(cfg.get("branding", {}).get("safe_area_pad", 60))
        pos = str(cfg.get("branding", {}).get("banner_position", "top")).lower()
        xy = f"(W-w)/2:{pad}" if pos == "top" else (f"(W-w)/2:H-h-{pad}" if pos == "bottom" else f"(W-w)/2:(H-h)/2")
        vf_nodes.append(f"[1:v]format=rgba,scale=min({target_w}-2*{pad}\\,iw):-2,scale=iw:min({int(cfg.get('branding',{}).get('banner_height_px',280))}\\,ih)[ovl]")
        vf_nodes.append(f"{current}[ovl]overlay={xy}[v4]")
        current = "[v4]"
        overlay_added = True

    # 4) Формат — гарантируем совместимость до субтитров
    vf_nodes.append(f"{current}format=yuv420p[v5]")
    current = "[v5]"

    # 5) Субтитры — ПОСЛЕ overlay/format
    style = _build_sub_style(cfg)
    vf_nodes.append(f"{current}subtitles=f='{srt_escape(srt_path)}':charenc=UTF-8:force_style='{style}'[v6]")
    current = "[v6]"

    # 6) Видео-фейды
    if to > (fade_in + fade_out + 0.1):
        vf_nodes.append(f"{current}fade=t=in:st=0:d={fade_in}[v7]")
        vf_nodes.append(f"[v7]fade=t=out:st={max(0.0, to - fade_out)}:d={fade_out}[vout]")
        current = "[vout]"
    else:
        vf_nodes.append(f"{current}copy[vout]")

    # 7) Аудио-цепочка (без смены темпа здесь!)
    a_nodes: List[str] = []
    if has_audio:
        a_nodes.append("[0:a]anull[a0]")
        if to > (fade_in + fade_out + 0.1):
            a_nodes.append(f"[a0]afade=t=in:st=0:d={fade_in}[a1]")
            a_nodes.append(f"[a1]afade=t=out:st={max(0.0, to - fade_out)}:d={fade_out}[aout]")
        else:
            a_nodes.append("[a0]anull[aout]")
    else:
        # тишина на выходе, если звука нет
        a_nodes.append("anullsrc=channel_layout=stereo:sample_rate=48000[aout]")

    filter_complex = ";".join(vf_nodes + a_nodes)

    # Сборка аргументов: точный seek → -ss ПОСЛЕ -i, явные map
    tmp_out = out_path.with_suffix(".prepro.mp4")
    args = ["-y", "-hide_banner", "-loglevel", "error"]
    args += ["-i", str(input_path)]
    if overlay_added:
        args += ["-i", str(overlay_img)]
    args += [
        "-ss", f"{ss:.3f}",
        "-t", f"{to:.3f}",
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-r", str(cfg.get("processing", {}).get("target_fps", 30)),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        "-avoid_negative_ts", "make_zero",
        "-shortest",
        str(tmp_out),
    ]
    LOG.info("FFmpeg: ffmpeg %s", " ".join(args))

    run_ffmpeg(args)

    # Финальный постпроцесс (нормализация/музыка/дакинг/единый темп) — опционально
    try:
        enhance_postprocess(tmp_out, out_path, cfg)
    except Exception as e:
        LOG.warning("postprocess failed (%s), keep prepro", e)
        # если постпроцесс не используешь — просто перекинем файл
        try:
            if out_path.exists():
                out_path.unlink()
        except Exception:
            pass
        os.replace(tmp_out, out_path)
        return

    # почистим преролл
    try:
        os.remove(tmp_out)
    except Exception:
        pass
