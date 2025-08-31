# pipeline/framing.py
from pathlib import Path
import subprocess, tempfile, os, math

# Опционально используем OpenCV для распознавания лиц; если его нет — fallback
try:
    import cv2
    _HAS_CV2 = True
except Exception:
    cv2 = None
    _HAS_CV2 = False

def _ffmpeg_grab_frame(src: Path, t: float, out_w: int = 960) -> Path:
    """Извлекает 1 кадр в JPEG во временную папку (масштаб до ширины out_w для скорости)."""
    tmp = Path(tempfile.gettempdir()) / f"frame_{abs(hash((str(src), t)))%10**9}.jpg"
    vf = f"scale={out_w}:-2"
    cmd = ["ffmpeg","-y","-hide_banner","-loglevel","error","-ss", f"{t:.3f}","-i", str(src),
           "-frames:v","1","-vf", vf, str(tmp)]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0 or not tmp.exists():
        raise RuntimeError("ffmpeg frame grab failed:\n" + p.stdout)
    return tmp

def _detect_interest(img_path: Path):
    """Возвращает (cx, cy, w, h) интересной области в координатах этого изображения."""
    if _HAS_CV2:
        img = cv2.imread(str(img_path))
        if img is None:
            raise RuntimeError("cv2.imread failed")
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # Лица → самый надёжный якорь
        casc_path = getattr(cv2.data, "haarcascades", "")
        faces = []
        for name in ["haarcascade_frontalface_default.xml",
                     "haarcascade_profileface.xml"]:
            cpath = os.path.join(casc_path, name)
            if os.path.isfile(cpath):
                face_cascade = cv2.CascadeClassifier(cpath)
                det = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
                for (x,y,w,h) in det:
                    faces.append((x,y,w,h))
        if faces:
            # Возьмём объединённый bbox лиц (если много)
            x0 = min(x for (x,_,w,_) in faces)
            y0 = min(y for (_,y,_,_) in faces)
            x1 = max(x+w for (x,_,w,_) in faces)
            y1 = max(y+h for (_,y,_,h) in faces)
            return ( (x0+x1)/2, (y0+y1)/2, (x1-x0), (y1-y0) )

        # Fallback: «салсиенс» по градиенту
        sobx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        soby = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        mag = cv2.magnitude(sobx, soby)
        _, thr = cv2.threshold(mag, 0, 255, cv2.THRESH_OTSU)
        thr = (mag > thr).astype("uint8") * 255
        cnts, _ = cv2.findContours(thr, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if cnts:
            c = max(cnts, key=cv2.contourArea)
            x,y,w,h = cv2.boundingRect(c)
            return (x + w/2, y + h/2, w, h)
        # В крайнем случае — центр кадра
        h, w = gray.shape
        return (w/2, h/2, w*0.3, h*0.3)
    else:
        # Без cv2: пусть будет центр
        # Размер вернём как 30% кадра, чтобы потом можно было чуть сместить кроп
        from PIL import Image
        with Image.open(str(img_path)) as im:
            w, h = im.size
        return (w/2, h/2, w*0.3, h*0.3)

def suggest_crop(src: Path, start: float, duration: float,
                 src_w: int, src_h: int,
                 target_w: int, target_h: int,
                 samples: int = 12) -> tuple[float,float]:
    """
    Возвращает смещение кропа (x, y) в СКАЛИРОВАННЫХ координатах после
    scale=target_w:target_h:force_original_aspect_ratio=increase.

    Для landscape (src_ar >= tgt_ar): после такого scale высота == target_h, ширина == target_h * src_ar.
    Тогда можно посчитать x в пикселях скейленного пространства.
    """
    src_ar = src_w / src_h
    tgt_ar = target_w / target_h
    # ширина/высота после scale (increase)
    if src_ar >= tgt_ar:
        scaled_w = target_h * src_ar
        scaled_h = target_h
        scale = target_h / src_h
    else:
        scaled_w = target_w
        scaled_h = target_w / src_ar
        scale = target_w / src_w

    # Выборка кадров по времени
    times = [start + (i + 0.5) * (duration / max(1, samples)) for i in range(max(1, samples))]
    cxs, cys = [], []
    for t in times:
        try:
            fp = _ffmpeg_grab_frame(src, t)
            cx, cy, _, _ = _detect_interest(fp)
            cxs.append(cx * scale)
            cys.append(cy * scale)
        finally:
            try: os.remove(fp)
            except Exception: pass

    if not cxs:
        # центр
        cxs = [scaled_w/2]; cys = [scaled_h/2]

    # усредняем, ограничиваем в пределах возможного кропа
    cx = sum(cxs) / len(cxs)
    cy = sum(cys) / len(cys)

    x = max(0.0, min(cx - target_w/2, max(0.0, scaled_w - target_w)))
    y = max(0.0, min(cy - target_h/2, max(0.0, scaled_h - target_h)))
    return (x, y)
