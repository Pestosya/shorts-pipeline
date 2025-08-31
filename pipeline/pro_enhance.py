# pipeline/pro_enhance.py
from pathlib import Path
import os, random, shutil, subprocess, json

def _run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError("FFmpeg failed\n" + " ".join(str(x) for x in cmd) + "\n\n" + p.stdout)
    return p.stdout

def _has_audio(path: Path) -> bool:
    cmd = ["ffprobe","-v","error","-print_format","json","-show_streams","-show_format", str(path)]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        return False
    try:
        j = json.loads(p.stdout)
        return any(s.get("codec_type") == "audio" for s in j.get("streams", []))
    except Exception:
        return False

def enhance_postprocess(in_path: Path, out_path: Path, cfg: dict):
    """
    Финальная полировка:
      - Вертикализация: keep | blur_bg | center_crop | letterbox
      - Нормализация речи
      - Фоновая музыка (+ дакинг)
      - Вариация скорости (и на видео, и на аудио) — чтобы не разъезжались субтитры
    """
    pro = cfg.get("pro", {})
    if not pro.get("enable_postprocess", True):
        if Path(in_path) != Path(out_path):
            out_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(in_path), str(out_path))
        return

    width  = int(pro.get("target_width", 1080))
    height = int(pro.get("target_height", 1920))
    fps    = int(pro.get("fps", 30))
    mode   = str(pro.get("vertical_mode", "keep")).lower()  # <-- БЫЛО отсутсвие: теперь точно есть

    # Подбор музыки (опционально)
    music = None
    mdir = pro.get("music_dir", "data/music")
    if pro.get("music_enabled", True) and os.path.isdir(mdir):
        files = [os.path.join(mdir, n) for n in os.listdir(mdir)
                 if n.lower().endswith((".mp3",".wav",".m4a",".aac",".flac"))]
        if files:
            music = random.choice(files)

    # Видеофильтры
    vf = ["[0:v]scale=-2:-2,setsar=1[v0]"]
    vmap = "[v0]"

    # Вертикализация
    if mode == "blur_bg":
        vf.append(f"[v0]scale={width}:{height}:force_original_aspect_ratio=increase,boxblur=luma_radius=20:luma_power=1:chroma_radius=20:chroma_power=1[bg]")
        vf.append(f"[v0]scale={width}:-2:force_original_aspect_ratio=decrease[fg]")
        vf.append(f"[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1[v1]"); vmap = "[v1]"
    elif mode == "center_crop":
        vf.append(f"[v0]scale={width*2}:{height*2}:force_original_aspect_ratio=increase[cfg]")
        vf.append(f"[cfg]crop={width}:{height}:(iw-{width})/2:(ih-{height})/2[v1]"); vmap = "[v1]"
    elif mode == "letterbox":
        vf.append(f"[v0]scale=-2:{height}:force_original_aspect_ratio=decrease[fg]")
        vf.append(f"[fg]pad={width}:{height}:(ow-iw)/2:(oh-ih)/2[v1]"); vmap = "[v1]"
    # keep — оставляем как есть

    # Лёгкая вариация скорости — применяется и к видео, и к аудио (чтобы сабы не уехали)
    spd = None
    if pro.get("speed_variation", True):
        spd = random.uniform(0.98, 1.02)  # безопасный диапазон для atempo
        vf.append(f"{vmap}setpts={1.0/spd}*PTS[v2]"); vmap = "[v2]"

    # Аудио
    has_a = _has_audio(in_path)
    a_filters = []
    inputs = ["-i", str(in_path)]
    music_index = None

    if music and os.path.isfile(music):
        inputs += ["-i", str(music)]
        music_index = 1

    if has_a:
        amapsrc = "[0:a]"
        if pro.get("speech_normalize", True):
            a_filters.append(f"{amapsrc}dynaudnorm=f=250:g=15:n=1[a0]"); amapsrc = "[a0]"
        if spd is not None:
            # синхронизируем аудио со скоростью видео
            a_filters.append(f"{amapsrc}atempo={spd:.6f}[a1]"); amapsrc = "[a1]"

        if music_index is not None:
            mv = float(pro.get("music_volume", 0.25))
            if pro.get("ducking", True):
                a_filters.append(f"[{music_index}:a]pan=stereo|c0=c0|c1=c1,adelay=0|0,volume={mv}[bg]")
                a_filters.append(f"[bg]{amapsrc}sidechaincompress=threshold=0.05:ratio=10:attack=20:release=500[ducked]")
                a_filters.append(f"{amapsrc}[ducked]amix=inputs=2:duration=first:dropout_transition=2[aout]")
            else:
                a_filters.append(f"{amapsrc}[{music_index}:a]amix=inputs=2:duration=first:dropout_transition=2,volume=1.0[aout]")
        else:
            a_filters.append(f"{amapsrc}anull[aout]")

        audio_map = "[aout]"
        audio_args = ["-c:a","aac","-b:a","160k"]
    else:
        # нет исходного звука → только музыка или синтетическая тишина
        if music_index is not None:
            a_filters.append(f"[{music_index}:a]pan=stereo|c0=c0|c1=c1,volume={float(pro.get('music_volume',0.25))}[aout]")
            audio_map = "[aout]"
        else:
            # генерим тишину внутри filter_complex
            a_filters.insert(0, "anullsrc=channel_layout=stereo:sample_rate=48000[aout]")
            audio_map = "[aout]"
        audio_args = ["-c:a","aac","-b:a","160k"]

    filter_complex = ";".join(vf + a_filters)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg","-y","-hide_banner","-loglevel","error"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", vmap, "-map", audio_map,
        "-r", str(fps),
        "-c:v","libx264","-preset","veryfast","-crf","20",
        "-pix_fmt","yuv420p",
        *audio_args,
        "-movflags","+faststart",
        "-shortest",  # выравниваем длительности
        str(out_path)
    ]
    _run(cmd)
