
import json, os, time
from pathlib import Path
from typing import Dict, Any, List, Optional
from .utils import LOG

def _load_queue(qfile: Path) -> Dict[str, Any]:
    if not qfile.exists():
        return {"items": []}
    try:
        with open(qfile, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"items": []}

def _save_queue(qfile: Path, data: Dict[str, Any]):
    qfile.parent.mkdir(parents=True, exist_ok=True)
    tmp = qfile.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, qfile)

def enqueue(qfile: Path, item: Dict[str, Any]):
    q = _load_queue(qfile)
    if not any(x.get("video_path")==item["video_path"] for x in q["items"]):
        q["items"].append(item)
        _save_queue(qfile, q)
        LOG.info("Enqueued %s", item["video_path"])

def pop_next(qfile: Path) -> Optional[Dict[str, Any]]:
    q = _load_queue(qfile)
    if not q["items"]:
        return None
    item = q["items"].pop(0)
    _save_queue(qfile, q)
    return item

def size(qfile: Path) -> int:
    return len(_load_queue(qfile)["items"])

def list_items(qfile: Path) -> List[Dict[str, Any]]:
    return _load_queue(qfile)["items"]
