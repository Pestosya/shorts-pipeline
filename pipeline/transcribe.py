from pathlib import Path
from typing import Dict, Any
import logging, srt
from faster_whisper import WhisperModel

LOG = logging.getLogger("pipeline.transcribe")

def _extract_wav_16k(src: Path) -> Path:
    tmp = Path(tempfile.gettempdir()) / (src.stem + ".16k_mono.wav")
    cmd = [
        "ffmpeg","-y","-hide_banner","-loglevel","error",
        "-i", str(src),
        "-vn", "-ac","1","-ar","16000","-f","wav",
        str(tmp)
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0 or not tmp.exists():
        raise RuntimeError("ffmpeg failed to extract wav:\n" + p.stdout)
    return tmp

def transcribe_file(path: Path, language: str, model_name: str, use_gpu: bool, device_index: int):
    device = "cuda" if use_gpu else "cpu"
    compute = "float16" if use_gpu else "int8"
    model = WhisperModel(model_name, device=device, device_index=device_index, compute_type=compute)

    opts = dict(
        language=language or "ru",
        vad_filter=True,
        vad_threshold=0.35,
        beam_size=5,
        best_of=5,
    )

    # Пытаемся транскрибировать напрямую
    try:
        it, info = model.transcribe(str(path), **opts)
    except Exception as e:
        # Fallback: вытащим WAV 16 кГц и транскрибируем его
        wav = _extract_wav_16k(Path(path))
        try:
            it, info = model.transcribe(str(wav), **opts)
        finally:
            try:
                os.remove(wav)
            except OSError:
                pass

    segments = []
    for seg in it:
        segments.append(dict(start=seg.start, end=seg.end, text=seg.text.strip()))
    return {"segments": segments, "language": getattr(info, "language", language)}