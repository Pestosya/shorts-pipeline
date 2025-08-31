import random, hashlib, os
from typing import Dict, Any, Tuple, List

def _rand_in(a: float, b: float, r: random.Random) -> float:
    return a + (b-a)*r.random()

def _choose_banner_or_watermark(cfg: Dict[str, Any], r: random.Random) -> Tuple[str, Dict[str,str]]:
    mode = cfg["branding"].get("mode", "watermark")
    wm = (cfg["branding"].get("watermark") or "").strip()
    bn = (cfg["branding"].get("banner") or "").strip()

    if mode == "watermark":
        return "watermark", {"image": wm}
    if mode == "banner":
        return "banner", {"image": bn}

    # mixed
    p = float(cfg["branding"].get("banner_probability", 0.35))
    if r.random() < p:
        return "banner", {"image": bn}
    return "watermark", {"image": wm}

def build_antidetect_filters(seed_key: str, cfg: Dict[str, Any], clip_duration: float, base_sub_fontsize: int=30) -> Dict[str, Any]:
    """
    Возвращает фильтры ffmpeg:
      - vf: список видео-фильтров
      - af: список аудио-фильтров
      - subtitle_fontsize: рекомендуемый кегль сабов (дальше подправится в edit.py)
      - overlay: {type, image, filter} — строка overlay для подключения к видеопотоку
    """
    h = hashlib.sha1(seed_key.encode("utf-8")).hexdigest()
    r = random.Random(int(h[:8], 16))

    vfs: List[str] = []
    afs: List[str] = []

    # Лёгкая цветокоррекция и текстуры (деликатно, без «пережарки»)
    a_cfg = cfg.get("antidetect", {})
    if a_cfg.get("enable", True):
        if r.random() < float(a_cfg.get("eq_prob", 0.8)):
            sat = round(_rand_in(1.03, 1.12, r), 2)
            cont = round(_rand_in(1.03, 1.10, r), 2)
            bri = round(_rand_in(-0.01, 0.03, r), 3)
            vfs.append(f"eq=saturation={sat}:contrast={cont}:brightness={bri}")
        if r.random() < float(a_cfg.get("noise_prob", 0.4)):
            vfs.append(f"noise=alls={int(a_cfg.get('noise_strength', 3))}:allf=t+u")
        if r.random() < float(a_cfg.get("unsharp_prob", 0.6)):
            ms = int(a_cfg.get("unsharp_luma_msize", 5))
            amt = float(a_cfg.get("unsharp_luma_amount", 1.0))
            vfs.append(f"unsharp={ms}:{ms}:{amt}")
        # аудио — лёгкая вариация темпа
        atempo = max(0.97, min(1.03, 1.0 + r.uniform(-0.02, 0.02)))
        afs.append(f"atempo={atempo:.3f}")

    # Базовый размер субтитров
    d0, d1 = a_cfg.get("subtitle_fontsize_delta", [-2, 2])
    sub_fs = base_sub_fontsize + int(round(_rand_in(d0, d1, r)))

    # Оверлей: watermark или banner — только если файл реально существует
    overlay_type, data = _choose_banner_or_watermark(cfg, r)
    overlay_filter = ""
    img = (data.get("image") or "").strip()

    if overlay_type == "watermark" and img and os.path.isfile(img):
        pos = (cfg["branding"].get("watermark_pos") or "10:10").split(":")
        x = pos[0] if len(pos) > 0 else "10"
        y = pos[1] if len(pos) > 1 else "10"
        # небольшое масштабирование, чтобы не закрывал кадр
        overlay_filter = f"[base][wm] overlay={x}:{y}"
        # ожидаем, что в edit.py перед overlay есть алиасы [base] и [wm] при необходимости
    elif overlay_type == "banner" and img and os.path.isfile(img):
        pos = (cfg["branding"].get("banner_position") or "top")
        pad = int(cfg["branding"].get("safe_area_pad", 60))
        max_h = int(cfg["branding"].get("banner_height_px", 280))
        if pos == "top":
            xy = f"(W-w)/2:{pad}"
        elif pos == "bottom":
            xy = f"(W-w)/2:H-h-{pad}"
        else:
            xy = f"(W-w)/2:(H-h)/2"
        t_end_appear = max(0.0, clip_duration - 1.0)
        overlay_filter = (
            f"[vid][ovl] overlay={xy}:shortest=1"
            f":enable='between(t,0,1.0)+between(t,{t_end_appear:.3f},{clip_duration:.3f})'"
        )
    else:
        overlay_type = "none"
        overlay_filter = ""

    return {
        "vf": vfs,
        "af": afs,
        "subtitle_fontsize": sub_fs,
        "overlay": {"type": overlay_type, "image": img, "filter": overlay_filter}
    }

# --- Backward compatibility with legacy imports in main.py ---

# --- Backward compatibility for legacy imports and calls ---

def apply_antidetect_effects(*args, **kwargs):
    """
    Поддерживает два стиля вызова:

    1) Новый (рекомендуемый):
       apply_antidetect_effects(seed_key, cfg, clip_duration, base_sub_fontsize=30) -> dict,
       эквивалент build_antidetect_filters(...)

    2) Старый «файловый»:
       apply_antidetect_effects(in_path, out_path)              -> применить постпроцесс (или скопировать)
       apply_antidetect_effects(in_path, out_path, cfg)         -> то же, с конфигом
       Где in_path / out_path — пути к файлам видео.

    Для (2) используется pro_enhance.enhance_postprocess, если он есть.
    Иначе — просто копируем вход в выход (no-op).
    """
    from pathlib import Path
    import shutil

    # Вариант 2: оба первых аргумента — пути (str/Path)
    if len(args) >= 2 and isinstance(args[0], (str, Path)) and isinstance(args[1], (str, Path)):
        in_path = Path(args[0])
        out_path = Path(args[1])
        cfg = args[2] if len(args) >= 3 else {}

        # Если перепутали порядок и существует только второй — меняем местами
        if not in_path.exists() and out_path.exists():
            in_path, out_path = out_path, in_path

        try:
            # Если есть модуль постпроцесса — применим его
            from .pro_enhance import enhance_postprocess
            # Если конфиг не передали — отключим постпроцесс явно
            if not cfg:
                cfg = {"pro": {"enable_postprocess": False}}
            enhance_postprocess(in_path, out_path, cfg)
        except Exception:
            # Фолбэк: просто копируем файл
            if in_path != out_path:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(in_path, out_path)
        return out_path

    # Вариант 1: «новая» сигнатура → проксируем к build_antidetect_filters
    seed_key = args[0]
    cfg = args[1]
    clip_duration = args[2]
    base_sub_fontsize = kwargs.get("base_sub_fontsize", 30)
    return build_antidetect_filters(seed_key, cfg, clip_duration, base_sub_fontsize)


def modify_audio(*args, **kwargs):
    """
    Историческая функция. Сейчас аудиофильтры формируются в build_antidetect_filters/edit.py.
    Для совместимости:
      - если передали список фильтров, вернём его как есть;
      - в остальных случаях — no-op.
    """
    if args:
        return args[0]
    return []

