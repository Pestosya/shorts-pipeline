import os, sys, yaml, logging, subprocess, shutil, json, re
from pathlib import Path
from datetime import datetime

LOG = logging.getLogger("pipeline")

def setup_logging(log_dir: Path):
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "pipeline.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.FileHandler(str(log_file), encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)]
    )

def load_config(cfg_path: Path):
    with open(cfg_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def ensure_ffmpeg():
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg не найден в PATH")

def run_ffmpeg(args, check=True):
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args
    LOG.info("FFmpeg: %s", " ".join(cmd))
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "ignore"))
    return p

def ffprobe_json(path: Path):
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", str(path)
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "ignore"))
    return json.loads(p.stdout.decode("utf-8"))

def media_info(path: Path):
    j = ffprobe_json(path)
    v = next((s for s in j["streams"] if s.get("codec_type")=="video"), {})
    a = next((s for s in j["streams"] if s.get("codec_type")=="audio"), {})
    dur = float(j.get("format",{}).get("duration", 0.0))
    return dict(
        width=int(v.get("width",1920)),
        height=int(v.get("height",1080)),
        duration=dur,
        has_audio=bool(a),
        fps=eval(v.get("r_frame_rate","30/1")) if v.get("r_frame_rate") else 30.0
    )

def slugify(s: str) -> str:
    s = re.sub(r"\s+", "-", s.strip()).lower()
    s = re.sub(r"[^a-z0-9\-_а-яё]", "", s)
    return s

def derive_show_from_filename(path: Path):
    name = path.stem

    # Популярные паттерны для серий
    patterns = [
        # Паттерн: Название Сериала [Сезон] [Эпизод]
        r"(.*?)[\s._-]*[Ss](\d+)[\s._-]*[Ee](\d+)",
        # Паттерн: Название Сериала - Эпизод XX
        r"(.*?)[\s._-]*[Ee]pisode[\s._-]*(\d+)",
        # Паттерн: Название Сериала - Сезон X Эпизод Y
        r"(.*?)[\s._-]*[Ss]eason[\s._-]*(\d+)[\s._-]*[Ee]pisode[\s._-]*(\d+)",
        # Паттерн: Название Сериала - Часть X
        r"(.*?)[\s._-]*[Pp]art[\s._-]*(\d+)",
        # Паттерн: Название Сериала - Серия XX
        r"(.*?)[\s._-]*[Сс]ерия[\s._-]*(\d+)",
        # Паттерн: Просто цифры в конце (01, 02 и т.д.)
        r"(.*?)[\s._-]*(\d{2})[^\/]*$"
    ]

    show = "Series"
    season = None
    episode = None

    for pattern in patterns:
        match = re.search(pattern, name, re.IGNORECASE)
        if match:
            if len(match.groups()) >= 2:
                show = match.group(1).strip()
                # Убираем лишние символы из названия
                show = re.sub(r"[._\-]", " ", show)
                show = re.sub(r"\s+", " ", show).strip()

                if len(match.groups()) >= 3:
                    season = int(match.group(2))
                    episode = int(match.group(3))
                else:
                    episode = int(match.group(2))
                    season = 1  # По умолчанию первый сезон
            break

    # Если не нашли паттерн, пробуем извлечь просто название
    if show == "Series":
        # Убираем цифры и расширения в конце
        show = re.sub(r"[\s._\-]*\d+.*$", "", name)
        show = re.sub(r"[._\-]", " ", show)
        show = re.sub(r"\s+", " ", show).strip()
        show = show or "Series"

    # Убираем common words из названия
    common_words = ['tv', 'season', 'sezon', 'сезон', 'part', 'часть', 'episode', 'серия', 'video', 'film']
    show_words = show.split()
    show = ' '.join([word for word in show_words if word.lower() not in common_words])

    return show, season, episode

def read_meta_yaml(path: Path):
    meta = path.with_suffix(path.suffix + ".meta.yaml")
    if meta.exists():
        with open(meta, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}

def srt_escape(p: Path) -> str:
    """
    Возвращает строку для ffmpeg filter_complex subtitles:
    - Windows-пути переводим в POSIX (forward slashes)
    - Если абсолютный путь с буквой диска, экранируем двоеточие после буквы (C\:/...)
    - Экранируем одинарные кавычки
    """
    p = Path(p)
    s = p.as_posix()              # backslashes -> forward slashes
    # Абсолютный Windows-путь: 'C:/...' → 'C\:/...'
    if len(s) >= 2 and s[1] == ':' and s[0].isalpha():
        s = s[0] + r'\:' + s[2:]
    s = s.replace("'", r"\'")
    return s
