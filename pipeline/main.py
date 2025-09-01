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