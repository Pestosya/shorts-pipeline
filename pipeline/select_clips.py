
from typing import List, Dict, Any
import re

def _score_text(txt: str, keywords: List[str]) -> float:
    t = txt.lower()
    score = 0.0
    for kw in keywords:
        if kw.lower() in t:
            score += 1.0
    return score

def pick_clips(segments: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    min_len = cfg["processing"]["min_clip_len_sec"]
    max_len = cfg["processing"]["max_clip_len_sec"]
    kw = cfg["selection"]["keywords"]
    kw_w = cfg["selection"]["keyword_weight"]
    sp_w = cfg["selection"]["speech_rate_weight"]
    vol_w = cfg["selection"]["volume_weight"]

    i = 0
    candidates = []
    n = len(segments)
    while i < n:
        start = segments[i]["start"]
        j = i
        acc_text = []
        while j < n and (segments[j]["end"] - start) < max_len:
            acc_text.append(segments[j]["text"])
            dur = segments[j]["end"] - start
            if dur >= min_len:
                text = " ".join(acc_text)
                speech_density = len(text) / max(1.0, dur)
                kw_score = _score_text(text, kw)
                score = kw_w*kw_score + sp_w*speech_density + vol_w*0.0
                candidates.append({
                    "start": start,
                    "end": segments[j]["end"],
                    "score": score,
                    "text": text
                })
            j += 1
        i += 1

    candidates.sort(key=lambda x: x["score"], reverse=True)
    picked = []
    for c in candidates:
        overlap = False
        for p in picked:
            inter = max(0.0, min(p["end"], c["end"]) - max(p["start"], c["start"]))
            if inter > 0 and inter / (c["end"]-c["start"]) > 0.4:
                overlap = True
                break
        if not overlap:
            words = re.findall(r"\w+", c["text"])
            clip_title = " ".join(words[:8]) or "Лучший момент"
            c["clip_title"] = clip_title
            picked.append(c)
        if len(picked) >= cfg["processing"]["max_clips_per_episode"]:
            break
    return picked
