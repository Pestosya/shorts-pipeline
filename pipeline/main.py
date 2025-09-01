from pathlib import Path
import os, logging, srt, yaml, shutil
from .utils import setup_logging, load_config, ensure_ffmpeg, media_info, slugify, derive_show_from_filename, read_meta_yaml
from .transcribe import transcribe_file
from .select_clips import pick_clips
from .edit import render_clip

LOG = logging.getLogger("pipeline.main")

def _soft_break_long_tokens(text: str, max_token: int = 12) -> str:
    """
    Вставляем нулевую ширину пробел (U+200B) внутрь очень длинных слов,
    чтобы libass мог переносить строку. Например: "супердлинноеслово" → "супердлинно\u200bеслово".
    """
    out = []
    for tok in text.split():
        if len(tok) > max_token:
            chunks = [tok[i:i+max_token] for i in range(0, len(tok), max_token)]
            out.append("\u200b".join(chunks))
        else:
            out.append(tok)
    return " ".join(out)

def _wrap_lines_cfg(text: str, cfg: dict) -> str:
    import textwrap
    subcfg = cfg.get("subtitles", {})
    width = int(subcfg.get("max_chars_per_line", 24))  # чуть уже по умолчанию
    max_lines = int(subcfg.get("max_lines", 2))
    # мягкий перенос длинных слов
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
    subs=[]
    idx=1
    for s in segments:
        if s["end"] <= t0 or s["start"] >= t1:
            continue
        st = max(t0, s["start"])
        en = min(t1, s["end"])
        if en - st < 0.01:
            continue
        content = _wrap_lines_cfg(s["text"], cfg)
        if not content:
            continue
        subs.append(srt.Subtitle(
            index=idx,
            start=srt.timedelta(seconds=st - t0),
            end=srt.timedelta(seconds=en - t0),
            content=content
        ))
        idx += 1
    return srt.compose(subs) if subs else ""




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