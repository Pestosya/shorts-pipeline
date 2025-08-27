from typing import List, Dict, Any
import re

def _score_text(txt: str, keywords: List[str]) -> float:
    t = txt.lower()
    score = 0.0
    # ключевые слова
    for kw in keywords:
        if kw.lower() in t:
            score += 2.0
    # плотность слов
    words = re.findall(r"\w+", t)
    score += min(3.0, len(words)/12.0)  # до +3
    # знаки препинания (эмоциональность)
    score += 0.3 * t.count("!") + 0.2 * t.count("?") + 0.1 * t.count(",")
    # цифры (факты/цифры часто удерживают внимание)
    if re.search(r"\d", t):
        score += 0.7
    # важные триггеры (можно расширить)
    for trig in ["лучший", "секрет", "важно", "итог", "главное", "ошибка", "лайфхак"]:
        if trig in t:
            score += 0.8
    return score

def pick_clips(segments: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    min_len = cfg["processing"]["min_clip_len_sec"]
    max_len = cfg["processing"]["max_clip_len_sec"]
    kw = cfg["selection"]["keywords"]

    # Собираем окна в заданном диапазоне длительности
    candidates: List[Dict[str, Any]] = []
    for seg in segments:
        s, e, text = float(seg["start"]), float(seg["end"]), seg["text"].strip()
        dur = max(0.01, e - s)
        if dur < min_len:  # короткий сегмент — попробуем расширить за счёт следующих
            continue
        # простой скоринг сегмента
        score = _score_text(text, kw)
        # штраф за слишком длинные куски (ближе к 60с — хуже)
        if dur > (max_len * 0.85):
            score -= 0.5
        candidates.append({"start": s, "end": min(e, s + max_len), "text": text, "score": score})

    # Сортируем по убыванию очков
    candidates.sort(key=lambda x: x["score"], reverse=True)

    # Жадно набираем без сильных пересечений
    picked: List[Dict[str, Any]] = []
    for c in candidates:
        dur = c["end"] - c["start"]
        if dur < min_len or dur > max_len:
            continue
        overlap = False
        for p in picked:
            inter = max(0.0, min(p["end"], c["end"]) - max(p["start"], c["start"]))
            if inter > 0 and inter / dur > 0.4:
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
