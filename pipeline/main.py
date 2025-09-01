from pathlib import Path
import os, logging, srt, yaml, shutil
from .utils import setup_logging, load_config, ensure_ffmpeg, media_info, slugify, derive_show_from_filename, read_meta_yaml
from .transcribe import transcribe_file
from .select_clips import pick_clips
from .edit import render_clip

LOG = logging.getLogger("pipeline.main")

from pathlib import Path
from typing import Dict, Any, List
import os, logging

from .utils import run_ffmpeg, srt_escape, media_info
from .antidetect import build_antidetect_filters
# --- SRT helpers: компактные строки, мягкие переносы, корректные тайминги ---
import srt as _srt
import textwrap

def _soft_break_long_tokens(text: str, max_token: int = 12) -> str:
    """
    Разбиваем очень длинные слова U+200B, чтобы libass мог переносить внутри слова.
    """
    out = []
    for tok in (text or "").split():
        if len(tok) > max_token:
            chunks = [tok[i:i+max_token] for i in range(0, len(tok), max_token)]
            out.append("\u200b".join(chunks))
        else:
            out.append(tok)
    return " ".join(out)

def _wrap_lines_cfg(text: str, cfg: dict) -> str:
    """
    Узкая колонка: не более max_chars_per_line символов и max_lines строк.
    """
    subcfg = cfg.get("subtitles", {})
    width = int(subcfg.get("max_chars_per_line", 24))
    max_lines = int(subcfg.get("max_lines", 2))
    safe = _soft_break_long_tokens(" ".join((text or "").split()), max_token=12)
    if not safe:
        return ""
    lines = textwrap.wrap(safe, width=width, break_long_words=False, break_on_hyphens=True)
    if len(lines) <= max_lines:
        return "\n".join(lines)
    trimmed = lines[:max_lines]
    if len(lines) > max_lines:
        if len(trimmed[-1]) >= max(4, width - 1):
            trimmed[-1] = trimmed[-1][:max(1, width - 1)] + "…"
        else:
            trimmed[-1] += "…"
    return "\n".join(trimmed)

def build_clip_srt(segments, t0, t1, cfg):
    """
    Строим сабы для клипа:
      - склеиваем сегменты с микропаузами (merge_gap_ms),
      - применяем глобальный смещение (offset_ms),
      - ограничиваем длительность строк по скорости чтения (reading_cps) и [min,max],
      - не даём строкам наезжать на следующий субтитр.
    """
    subcfg = cfg.get("subtitles", {})
    offset = int(subcfg.get("offset_ms", 0)) / 1000.0
    merge_gap = int(subcfg.get("merge_gap_ms", 180)) / 1000.0
    cps = float(subcfg.get("reading_cps", 14.0))
    min_d = int(subcfg.get("min_duration_ms", 420)) / 1000.0
    max_d = int(subcfg.get("max_duration_ms", 3200)) / 1000.0

    # 1) Берём сегменты, попадающие в окно клипа
    raw = []
    for s in segments:
        st, en = float(s["start"]), float(s["end"])
        if en <= t0 or st >= t1:
            continue
        st = max(t0, st); en = min(t1, en)
        if en - st < 0.02:
            continue
        raw.append({"start": st, "end": en, "text": (s["text"] or "").strip()})
    raw.sort(key=lambda x: x["start"])

    # 2) Склейка коротких пауз
    merged = []
    for seg in raw:
        if not merged:
            merged.append(seg); continue
        prev = merged[-1]
        if seg["start"] - prev["end"] <= merge_gap:
            prev["end"] = seg["end"]
            joiner = " " if (prev["text"] and seg["text"]) else ""
            prev["text"] = (prev["text"] + joiner + seg["text"]).strip()
        else:
            merged.append(seg)

    # 3) Построение итоговых сабов
    subs = []
    for i, m in enumerate(merged):
        st = (m["start"] + offset) - t0
        en = (m["end"] + offset) - t0
        if en - st < 0.02:
            continue

        next_st_abs = (merged[i+1]["start"] + offset) - t0 if i+1 < len(merged) else (t1 - t0)
        max_en_by_next = next_st_abs - 0.06  # небольшой зазор

        text_wrapped = _wrap_lines_cfg(m["text"], cfg)
        if not text_wrapped:
            continue

        # желаемая длительность: минимум — по исходному, но с ограничителями
        desired = max(min_d, min(max_d, max(en - st, len(m["text"]) / max(1e-6, cps))))
        en2 = min(st + desired, max_en_by_next, (t1 - t0))
        if en2 - st < 0.08:
            en2 = min(en, (t1 - t0))
        if en2 <= st:
            continue

        subs.append(_srt.Subtitle(
            index=len(subs)+1,
            start=_srt.timedelta(seconds=st),
            end=_srt.timedelta(seconds=en2),
            content=text_wrapped
        ))

    return _srt.compose(subs) if subs else ""


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
    for f in anti.get("vf", []):
        vf_nodes.append(f"{current}{',' if f and not f.strip().startswith('[') else ''}{f}[v3]")
        current = "[v3]"

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





def process_one(cfg, video_path: Path):
    out_dir = Path(cfg["paths"]["outputs"])
    proc_dir = Path(cfg["paths"]["processed"])
    out_dir.mkdir(parents=True, exist_ok=True)
    proc_dir.mkdir(parents=True, exist_ok=True)

    show, season, episode = derive_show_from_filename(video_path)
    meta = read_meta_yaml(video_path)
    show = meta.get("show", show)
    season = meta.get("season", season)
    episode = meta.get("episode", episode)
    channel_alias = meta.get("channel_alias") or cfg["channels"]["series_to_channel"].get(show, cfg["channels"]["series_to_channel"]["default"])

    LOG.info("Processing %s | show=%s S%sE%s → chan=%s", video_path.name, show, season, episode, channel_alias)
    t = transcribe_file(video_path, cfg["processing"]["language"], cfg["processing"]["whisper_model"], cfg["processing"]["use_gpu"], cfg["processing"]["device_index"])
    clips = pick_clips(t["segments"], cfg)
    LOG.info("Picked %d clips", len(clips))

    # Создаем хештег без пробелов и спецсимволов
    show_hashtag = "".join([c for c in show if c.isalnum()])
    season_ep = f"S{season or 1}E{episode or 1}"

    rendered = []
    for k, clip in enumerate(clips, 1):
        clip_srt_text = build_clip_srt(t["segments"], clip["start"], clip["end"], cfg)
        clip_srt_path = out_dir / f"{slugify(show)}_{season_ep}_clip{k}.srt"
        with open(clip_srt_path, "w", encoding="utf-8") as f:
            f.write(clip_srt_text)

        out_mp4 = out_dir / f"{slugify(show)}_{season_ep}_clip{k}.mp4"

        # Удаляем старый файл если существует
        if out_mp4.exists():
            out_mp4.unlink()

        render_clip(video_path, clip_srt_path, out_mp4, clip, cfg)

        # Удаляем временный SRT-файл после рендера
        if clip_srt_path.exists():
            clip_srt_path.unlink()
            LOG.debug("Удален временный файл: %s", clip_srt_path.name)

        # ВНИМАНИЕ: никаких temp_* больше не делаем —
        # render_clip уже вывел готовый финальный файл (музыка/дакинг/нормализация/скорость внутри edit.py)
        LOG.info("Saved %s", out_mp4.name)

        # Формируем заголовок с хештегом
        title = cfg["youtube"]["title_template"].format(
            show=show,
            clip_title=clip["clip_title"],
            show_nospace=show_hashtag,
            show_hashtag=show_hashtag
        )

        desc = cfg["youtube"]["description_template"].format(
            show=show,
            season_episode=season_ep,
            show_hashtag=show_hashtag
        )

        tags = cfg["youtube"]["tags"] + [show_hashtag]
        privacy = cfg["youtube"]["default_privacy"]
        cat = cfg["youtube"]["default_category_id"]
        playlist = cfg["channels"]["aliases"][channel_alias].get("playlist_id","")

        # # ЗАГРУЖАЕМ НА YOUTUBE
        # try:
        #     upload_short(cfg, channel_alias, out_mp4, title, desc, tags, privacy, str(cat), playlist)
        #     rendered.append(out_mp4)
        # except Exception as e:
        #     LOG.error("Ошибка загрузки на YouTube: %s", e)
        #     continue

    # Переносим исходник
    video_path.rename(proc_dir / video_path.name)

def main():
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.yaml")
    setup_logging(Path(cfg["paths"]["logs"]))
    ensure_ffmpeg()

    inbox = Path(cfg["paths"]["inbox"])
    files = sorted([p for p in inbox.iterdir() if p.suffix.lower() in (".mp4",".mkv",".mov",".m4v")])
    if not files:
        LOG.info("Inbox пуст")
        return
    for f in files:
        try:
            process_one(cfg, f)
        except Exception as e:
            LOG.exception("Ошибка обработки %s: %s", f.name, e)

if __name__ == "__main__":
    main()