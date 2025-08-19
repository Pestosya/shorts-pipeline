from pathlib import Path
from typing import Dict, Any
from .utils import run_ffmpeg, srt_escape

def render_clip(input_path: Path, srt_path: Path, out_path: Path, clip: Dict[str, Any], cfg: Dict[str, Any]):
    # Разрешение для вертикального видео (9:16)
    target_width = 1080
    target_height = 1920

    # Получаем информацию о исходном видео
    from .utils import media_info
    info = media_info(input_path)
    src_width = info["width"]
    src_height = info["height"]

    # Высота области для видео (3/4 экрана)
    video_area_height = target_height * 3 // 4
    # Высота области для субтитров (1/4 экрана)
    subtitle_area_height = target_height // 4

    # Масштабируем видео чтобы оно вписывалось в верхнюю область
    scale_w = target_width / src_width
    scale_h = video_area_height / src_height
    scale_factor = min(scale_w, scale_h)  # Используем min чтобы сохранить пропорции

    new_width = int(src_width * scale_factor)
    new_height = int(src_height * scale_factor)

    # Вычисляем паддинг для центрирования видео в верхней области
    pad_x = (target_width - new_width) // 2
    pad_y = (video_area_height - new_height) // 2

    # Основные фильтры для видео
    video_filters = [
        f"scale={new_width}:{new_height}",
        f"pad={target_width}:{video_area_height}:{pad_x}:{pad_y}:black"
    ]

    # Создаем черную плашку для субтитров (1/4 экрана внизу)
    subtitle_box_filter = f"drawbox=0:{video_area_height}:{target_width}:{subtitle_area_height}:black@1.0:t=fill"

    # Субтитры
    if srt_path.exists():
        # Стиль субтитров: белый текст на черном фоне
        subtitle_style = (
            "FontName=Arial,"
            "Fontsize=10,"  
            "PrimaryColour=&H00FFFFFF,"  # Белый текст
            "OutlineColour=&H00000000,"  # Черная обводка
            "BackColour=&HFF000000,"  # Черный непрозрачный фон
            "Bold=1,"  # Жирный шрифт
            "Outline=1,"  # Обводка
            "Shadow=0,"  # Без тени
            "Alignment=2,"  # По центру
            "MarginV=40,"  # Больший отступ от краев
            "MarginL=60,"  # Отступ слева
            "MarginR=60"   # Отступ справа
        )

        # Фильтр для субтитров - позиционируем в нижней области
        subtitle_filter = f"subtitles='{srt_escape(srt_path)}':force_style='{subtitle_style}'"

        # Собираем все фильтры вместе
        filter_complex = (
            f"[0:v]{','.join(video_filters)}[video]; "
            f"[video]{subtitle_box_filter}[video_with_box]; "
            f"[video_with_box]{subtitle_filter}[outv]"
        )
    else:
        # Если нет субтитров, просто создаем черную плашку
        filter_complex = (
            f"[0:v]{','.join(video_filters)}[video]; "
            f"[video]{subtitle_box_filter}[outv]"
        )

    # Водяной знак
    wm = cfg["branding"].get("watermark") or ""
    wm_path = Path(wm) if wm else None

    if wm_path and wm_path.exists():
        x, y = (cfg["branding"].get("watermark_pos") or "10:10").split(":")

        # Добавляем водяной знак к фильтр комплексу
        final_filter_complex = f"{filter_complex}; [outv][1:v]overlay={x}:{y}[final]"
        map_output = "[final]"

        af = [f"loudnorm=I={cfg['processing']['audio_lufs']}:LRA=11:TP=-1.5"]
        ss = max(0.0, clip["start"])
        to = max(0.1, clip["end"] - clip["start"])

        run_ffmpeg([
            "-ss", f"{ss:.3f}",
            "-i", str(input_path),
            "-i", str(wm_path),
            "-t", f"{to:.3f}",
            "-filter_complex", final_filter_complex,
            "-map", map_output,
            "-map", "0:a?",
            "-r", str(cfg["processing"]["target_fps"]),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "160k",
            "-af", ",".join(af),
            "-aspect", "9:16",
            str(out_path)
        ])
    else:
        # Без водяного знака
        final_filter_complex = filter_complex
        map_output = "[outv]"

        af = [f"loudnorm=I={cfg['processing']['audio_lufs']}:LRA=11:TP=-1.5"]
        ss = max(0.0, clip["start"])
        to = max(0.1, clip["end"] - clip["start"])

        run_ffmpeg([
            "-ss", f"{ss:.3f}",
            "-i", str(input_path),
            "-t", f"{to:.3f}",
            "-filter_complex", final_filter_complex,
            "-map", map_output,
            "-map", "0:a?",
            "-r", str(cfg["processing"]["target_fps"]),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "160k",
            "-af", ",".join(af),
            "-aspect", "9:16",
            str(out_path)
        ])