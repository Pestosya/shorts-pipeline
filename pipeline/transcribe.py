# pipeline/transcribe.py
from pathlib import Path
import subprocess, tempfile, os, logging
from faster_whisper import WhisperModel

LOG = logging.getLogger("pipeline.transcribe")

def _extract_wav_16k(src: Path) -> Path:
    """
    Надёжный фоллбэк: вытащить аудио в 16kHz mono WAV через ffmpeg.
    """
    tmp = Path(tempfile.gettempdir()) / (src.stem + ".16k_mono.wav")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-vn", "-ac", "1", "-ar", "16000", "-f", "wav",
        str(tmp)
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0 or not tmp.exists():
        raise RuntimeError("ffmpeg failed to extract wav:\n" + p.stdout)
    return tmp

def _load_model(model_name: str, use_gpu: bool, device_index: int):
    """
    Аккуратная загрузка модели с безопасным compute_type.
    """
    device = "cuda" if use_gpu else "cpu"
    compute = "float16" if use_gpu else "int8"
    try:
        return WhisperModel(model_name, device=device, device_index=device_index, compute_type=compute)
    except Exception:
        return WhisperModel(model_name, device=device, device_index=device_index, compute_type="float32")

def _do_transcribe(model: WhisperModel, src: str, opts: dict):
    """
    Вызывает model.transcribe с защитой:
    - если передан неподдерживаемый язык → повтор без language (автоопределение)
    """
    try:
        return model.transcribe(src, **opts)
    except ValueError as e:
        msg = str(e)
        if "not a valid language code" in msg:
            LOG.warning("Invalid language in opts; retrying with autodetect. Err: %s", msg)
            o2 = dict(opts)
            o2.pop("language", None)   # убрать язык → автоопределение
            return model.transcribe(src, **o2)
        raise

def transcribe_file(path: Path, language: str, model_name: str, use_gpu: bool, device_index: int):
    """
    Транскрибация с VAD и фоллбэком через WAV на случай проблемных контейнеров.
    Корректно обрабатывает language == 'auto' / '' / None.
    """
    model = _load_model(model_name, use_gpu, device_index)

    # Базовые опции (без нестабильных параметров)
    opts = dict(
        vad_filter=True,      # включаем стандартный VAD
        beam_size=5,
        best_of=5,
    )

    # Язык: 'auto' / '' / None → не передавать (автоопределение)
    lang_flag = (language or "").strip().lower()
    if lang_flag and lang_flag not in ("auto", "autodetect", "detect"):
        opts["language"] = lang_flag

    # 1) Пробуем исходный файл
    try:
        segments, info = _do_transcribe(model, str(path), opts)
    except Exception as e:
        LOG.warning("Direct transcribe failed on %s, fallback to WAV: %s", Path(path).name, e)
        # 2) Фоллбэк: вытащим WAV и транскрибируем его
        wav = _extract_wav_16k(Path(path))
        try:
            segments, info = _do_transcribe(model, str(wav), opts)
        finally:
            try:
                os.remove(wav)
            except OSError:
                pass

    out_segments = []
    for seg in segments:
        out_segments.append(dict(start=seg.start, end=seg.end, text=seg.text.strip()))

    lang = getattr(info, "language", None) or (language or "auto")
    return {"segments": out_segments, "language": lang}
