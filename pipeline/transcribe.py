from pathlib import Path
from typing import Dict, Any
import logging, srt

LOG = logging.getLogger("pipeline.transcribe")

def transcribe_file(path: Path, lang: str, model_name: str, use_gpu: bool, device_index: int) -> Dict[str, Any]:
    """
    Возвращает:
      {
        "segments": [ {"start": float, "end": float, "text": str} ],
        "language": "ru",
        "srt_text": "...",
      }
    """
    try:
        from faster_whisper import WhisperModel
    except Exception as e:
        raise RuntimeError("faster-whisper не установлен или нет CUDA") from e

    device = "cuda" if use_gpu else "cpu"
    compute_type = "float16" if use_gpu else "int8"
    model = WhisperModel(model_name, device=device, device_index=device_index, compute_type=compute_type)

    opts = dict(language=None if lang=="auto" else lang, vad_filter=True)
    LOG.info("Transcribing %s ...", path.name)

    segs = []
    lang_detected = None
    for seg in model.transcribe(str(path), **opts)[0]:
        segs.append({"start": float(seg.start), "end": float(seg.end), "text": seg.text.strip()})
        if getattr(seg, "language", None) and lang_detected is None:
            lang_detected = seg.language

    # SRT
    subs = []
    for i, s in enumerate(segs, 1):
        subs.append(srt.Subtitle(index=i, start=srt.timedelta(seconds=s["start"]), end=srt.timedelta(seconds=s["end"]), content=s["text"]))
    srt_text = srt.compose(subs)

    return {"segments": segs, "language": lang_detected or lang, "srt_text": srt_text}
