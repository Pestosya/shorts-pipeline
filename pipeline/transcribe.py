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
        # very safe fallback
        return WhisperModel(model_name, device=device, device_index=device_index, compute_type="float32")

def transcribe_file(path: Path, language: str, model_name: str, use_gpu: bool, device_index: int):
    """
    Транскрибация с VAD и фоллбэком через WAV на случай проблемных контейнеров.
    Никаких несовместимых параметров (vad_threshold) — только vad_filter=True.
    """
    model = _load_model(model_name, use_gpu, device_index)

    opts = dict(
        language=language or "ru",
        vad_filter=True,   # используем стандартные параметры VAD вашей версии faster-whisper
        beam_size=5,
        best_of=5,
    )

    # Пытаемся транскрибировать напрямую исходный файл
    try:
        segments, info = model.transcribe(str(path), **opts)
    except Exception as e:
        LOG.warning("Direct transcribe failed on %s, fallback to WAV: %s", path.name, e)
        wav = _extract_wav_16k(Path(path))
        try:
            segments, info = model.transcribe(str(wav), **opts)
        finally:
            try:
                os.remove(wav)
            except OSError:
                pass

    out_segments = []
    for seg in segments:
        out_segments.append(dict(start=seg.start, end=seg.end, text=seg.text.strip()))

    lang = getattr(info, "language", language or "ru")
    return {"segments": out_segments, "language": lang}
