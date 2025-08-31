from pathlib import Path
import os, random, shutil, subprocess, json

def _run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError("FFmpeg failed\n" + " ".join(cmd) + "\n\n" + p.stdout)
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
    ...
    vf = ["[0:v]scale=-2:-2,setsar=1[v0]"]
    vmap = "[v0]"

    # ---- ВЕРТИКАЛИЗАЦИЯ (как было) ----
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

    # ---- ЛЁГКАЯ ВАРИАЦИЯ СКОРОСТИ: ТЕПЕРЬ И ВИДЕО, И АУДИО ----
    spd = None
    if pro.get("speed_variation", True):
        spd = random.uniform(0.98, 1.02)  # чуть уже диапазон — стабильнее сабы
        vf.append(f"{vmap}setpts={1.0/spd}*PTS[v2]"); vmap = "[v2]"

    has_a = _has_audio(in_path)
    a_filters = []
    inputs = ["-i", str(in_path)]
    music_index = None
    if music and os.path.isfile(music):
        inputs += ["-i", str(music)]
        music_index = 1

    if has_a:
        amapsrc = "[0:a]"
        # нормализация речи (как было)
        if pro.get("speech_normalize", True):
            a_filters.append(f"{amapsrc}dynaudnorm=f=250:g=15:n=1[a0]"); amapsrc = "[a0]"
        # ДОБАВИЛИ синхронизирующий atempo, если есть speed-вар
        if spd is not None:
            a_filters.append(f"{amapsrc}atempo={spd:.6f}[a1]"); amapsrc = "[a1]"

        # музыка/дакинг (как было)
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
        # нет исходного аудио: будет только музыка (если есть) или синтетическая тишина
        if music_index is not None:
            a_filters.append(f"[{music_index}:a]pan=stereo|c0=c0|c1=c1,volume={float(pro.get('music_volume',0.25))}[aout]")
            audio_map = "[aout]"
        else:
            a_filters.insert(0, "anullsrc=channel_layout=stereo:sample_rate=48000[aout]")
            audio_map = "[aout]"
        audio_args = ["-c:a","aac","-b:a","160k"]

    filter_complex = ";".join(vf + a_filters)
    cmd = ["ffmpeg","-y","-hide_banner","-loglevel","error"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", vmap, "-map", audio_map,
        "-r", str(fps),
        "-c:v","libx264","-preset","veryfast","-crf","20",
        "-pix_fmt","yuv420p",
        *audio_args,
        "-movflags","+faststart",
        "-shortest",                 # ← гарантируем одинаковую длину треков
        str(out_path)
    ]
    _run(cmd)

