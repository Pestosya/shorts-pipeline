
import random, hashlib
from typing import Dict, Any, Tuple, List

def _rand_in(a: float, b: float, r: random.Random) -> float:
    return a + (b-a)*r.random()

def _choose_banner_or_watermark(cfg: Dict[str, Any], r: random.Random) -> Tuple[str, Dict[str,str]]:
    mode = cfg["branding"].get("mode", "watermark")
    wm = cfg["branding"].get("watermark") or ""
    bn = cfg["branding"].get("banner") or ""
    if mode == "watermark":
        return "watermark", {"image": wm}
    if mode == "banner":
        return "banner", {"image": bn}
    p = float(cfg["branding"].get("banner_probability", 0.35))
    if bn and r.random() < p:
        return "banner", {"image": bn}
    return "watermark", {"image": wm}

def build_antidetect_filters(seed_key: str, cfg: Dict[str, Any], clip_duration: float, base_sub_fontsize: int=30) -> Dict[str, Any]:
    '''
    Returns a dict with randomized ffmpeg filters to reduce cross-similarity and keep Shorts aesthetics.
    Keys:
      - vf: list[str] video filters (applied after scale/pad)
      - af: list[str] audio filters (applied after loudnorm)
      - subtitle_fontsize: int font size override
      - overlay: {type: 'watermark'|'banner', image: str, filter: str}
    '''
    h = hashlib.sha1(seed_key.encode("utf-8")).hexdigest()
    r = random.Random(int(h[:8], 16))

    vfs: List[str] = []
    afs: List[str] = []

    if cfg.get("antidetect", {}).get("enable", True):
        eq_cfg = cfg["antidetect"].get("eq", {})
        contrast = _rand_in(*eq_cfg.get("contrast", [1.0, 1.08]), r)
        saturation = _rand_in(*eq_cfg.get("saturation", [1.0, 1.12]), r)
        gamma = _rand_in(*eq_cfg.get("gamma", [0.98, 1.05]), r)
        vfs.append(f"eq=contrast={contrast:.3f}:saturation={saturation:.3f}:gamma={gamma:.3f}")

        if r.random() < float(cfg["antidetect"].get("unsharp_prob",0.6)):
            m = int(cfg["antidetect"].get("unsharp_luma_msize",5))
            a = float(cfg["antidetect"].get("unsharp_luma_amount",1.0))
            vfs.append(f"unsharp={m}:{m}:{a:.2f}")

        if r.random() < float(cfg["antidetect"].get("noise_prob",0.4)):
            strength = int(cfg["antidetect"].get("noise_strength",3))
            vfs.append(f"noise=alls={strength}:allf=t+u")

        if r.random() < float(cfg["antidetect"].get("vignette_prob",0.5)):
            v = float(cfg["antidetect"].get("vignette_strength",0.35))
            vfs.append(f"vignette=angle={v:.2f}")

        atempo = max(0.97, min(1.03, 1.0 + r.uniform(-0.02, 0.02)))
        afs.append(f"atempo={atempo:.3f}")

    d0, d1 = cfg.get("antidetect",{}).get("subtitle_fontsize_delta", [-2,2])
    sub_fs = base_sub_fontsize + int(round(_rand_in(d0, d1, r)))

    overlay_type, data = _choose_banner_or_watermark(cfg, r)
    overlay_filter = ""
    if overlay_type == "watermark" and data.get("image"):
        x, y = (cfg["branding"].get("watermark_pos") or "10:10").split(":")
        overlay_filter = f"overlay={x}:{y}"
    elif overlay_type == "banner" and data.get("image"):
        pos = cfg["branding"].get("banner_position","top")
        pad = int(cfg["branding"].get("safe_area_pad", 60))
        max_h = int(cfg["branding"].get("banner_height_px", 280))
        if pos == "top":
            xy = f"(W-w)/2:{pad}"
        elif pos == "bottom":
            xy = f"(W-w)/2:H-h-{pad}"
        else:
            xy = f"(W-w)/2:(H-h)/2"
        t_end_appear = max(0.0, clip_duration - 1.2)
        overlay_filter = (
            f"[banner] format=rgba,scale=min(1080-2*{pad}\\,iw):-2,scale=iw:min({max_h}\\,ih) [bn]; "
            f"[base][bn] overlay={xy}:enable='between(t,0,1.2)+between(t,{t_end_appear:.3f},{clip_duration:.3f})'"
        )
    return {"vf": vfs, "af": afs, "subtitle_fontsize": sub_fs, "overlay": {"type": overlay_type, "image": data.get("image",""), "filter": overlay_filter}}
