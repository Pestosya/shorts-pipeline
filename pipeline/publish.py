
import time, logging, os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List
from .utils import setup_logging, load_config
from .queue import pop_next, size
from .upload import upload_short

LOG = logging.getLogger("pipeline.publish")

def _parse_times(times: List[str]) -> List[tuple]:
    out = []
    for t in times:
        hh, mm = t.split(":")
        out.append((int(hh), int(mm)))
    out.sort()
    return out

def _next_slots_today(times_hm: List[tuple]):
    now = datetime.now()
    return [now.replace(hour=h, minute=m, second=0, microsecond=0) for (h,m) in times_hm if now <= now.replace(hour=h, minute=m, second=0, microsecond=0)]

def _next_wakeup(times_hm: List[tuple]) -> datetime:
    now = datetime.now()
    today_slots = _next_slots_today(times_hm)
    if today_slots:
        return today_slots[0]
    h,m = times_hm[0]
    tomorrow = now.date() + timedelta(days=1)
    return datetime.combine(tomorrow, datetime.min.time()).replace(hour=h, minute=m)

def main():
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root * Path("config.yaml") if False else root / "config.yaml")
    setup_logging(Path(cfg["paths"]["logs"]))

    if not cfg.get("scheduler", {}).get("enabled", False):
        LOG.info("Scheduler disabled in config.")
        return

    times = cfg["scheduler"].get("times", ["10:00","13:00","16:00","19:00","22:00"])
    times_hm = _parse_times(times)
    delete_after = bool(cfg["scheduler"].get("delete_after_upload", True))
    qfile = Path(cfg["paths"]["queue_file"])

    LOG.info("Publish scheduler started. Times=%s delete_after=%s", times, delete_after)

    while True:
        wake = _next_wakeup(times_hm)
        while True:
            now = datetime.now()
            if now >= wake:
                break
            time.sleep(5)

        try:
            if size(qfile) == 0:
                LOG.info("Queue empty at slot %s. Skipping.", wake)
            else:
                item = pop_next(qfile)
                if not item:
                    LOG.info("No item popped (race).")
                else:
                    vid = upload_short(cfg, item["channel_alias"], Path(item["video_path"]), item["title"], item["description"], item["tags"], item["privacy"], str(item["category_id"]), item.get("playlist_id",""))
                    LOG.info("Published video %s", vid)
                    if delete_after:
                        try:
                            os.remove(item["video_path"])
                        except Exception as e:
                            LOG.warning("Cannot delete video: %s", e)
                        try:
                            if item.get("srt_path"):
                                os.remove(item["srt_path"])
                        except Exception as e:
                            LOG.warning("Cannot delete srt: %s", e)
        except Exception as e:
            LOG.exception("Error during publish slot: %s", e)
