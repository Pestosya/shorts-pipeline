import random
from pathlib import Path
from typing import Optional
from .utils import run_ffmpeg
import shutil

def apply_antidetect_effects(input_path: Path, output_path: Path):
    """
    Применяет минимальные эффекты для обхода детекции авторских прав
    """
    if output_path.exists():
        output_path.unlink()
    
    # Только легкие, незаметные эффекты
    video_effects = []
    
    # 50% chance - очень небольшое изменение скорости (почти незаметное)
    if random.random() < 0.5:
        speed = random.uniform(0.99, 1.01)  # всего 1% изменение
        video_effects.append(f"setpts={speed}*PTS")
    
    # 40% chance - минимальное изменение цвета
    if random.random() < 0.4:
        saturation = random.uniform(0.99, 1.01)
        video_effects.append(f"hue=s={saturation}")
    
    # 30% chance - минимальное изменение контраста
    if random.random() < 0.3:
        contrast = random.uniform(0.99, 1.01)
        video_effects.append(f"eq=contrast={contrast}")
    
    # УБИРАЕМ зеркальное отражение и вращение - они слишком заметны
    # УБИРАЕМ зернистость - тоже заметно
    
    if video_effects:
        vf = ",".join(video_effects)
        run_ffmpeg([
            "-i", str(input_path),
            "-vf", vf,
            "-c:a", "copy",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-y",
            str(output_path)
        ])
    else:
        shutil.copy2(input_path, output_path)

def modify_audio(input_path: Path, output_path: Path):
    """
    Минимальные изменения аудио
    """
    if output_path.exists():
        output_path.unlink()
    
    # 50% chance - очень небольшое изменение pitch
    if random.random() < 0.5:
        pitch_change = random.uniform(0.999, 1.001)  # всего 0.1% изменение
        af = f"asetrate=44100*{pitch_change},aresample=44100"
        
        run_ffmpeg([
            "-i", str(input_path),
            "-af", af,
            "-c:v", "copy",
            "-y",
            str(output_path)
        ])
    else:
        shutil.copy2(input_path, output_path)