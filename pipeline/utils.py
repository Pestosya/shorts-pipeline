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
    m = re.search(r"[Ss](\d+)[Ee](\d+)", name)
    if m:
        season = int(m.group(1)); episode = int(m.group(2))
        show = re.sub(r"[._]", " ", re.sub(r"[Ss]\d+[Ee]\d+.*$", "", name)).strip()
        return show or "Series", season, episode
    return "Series", None, None

def read_meta_yaml(path: Path):
    meta = path.with_suffix(path.suffix + ".meta.yaml")
    if meta.exists():
        with open(meta, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}

def srt_escape(p: Path) -> str:
    # Правильные кавычки/слеши для Windows-пути в фильтре subtitles
    return str(p).replace("\\", "\\\\").replace(":", r"\:")
