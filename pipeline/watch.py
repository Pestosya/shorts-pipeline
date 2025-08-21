
from pathlib import Path
import time, logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from .main import process_one
from .utils import setup_logging, load_config, ensure_ffmpeg

LOG = logging.getLogger("pipeline.watch")

class Handler(FileSystemEventHandler):
    def __init__(self, cfg):
        self.cfg = cfg
    def on_created(self, event):
        if event.is_directory: return
        p = Path(event.src_path)
        if p.suffix.lower() not in (".mp4",".mkv",".mov",".m4v"): return
        LOG.info("New file: %s", p.name)
        time.sleep(3)
        try:
            process_one(self.cfg, p)
        except Exception as e:
            LOG.exception("Ошибка обработки %s: %s", p.name, e)

def main():
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.yaml")
    setup_logging(Path(cfg["paths"]["logs"]))
    ensure_ffmpeg()

    inbox = Path(cfg["paths"]["inbox"])
    inbox.mkdir(parents=True, exist_ok=True)

    ev = Handler(cfg)
    obs = Observer()
    obs.schedule(ev, str(inbox), recursive=False)
    obs.start()
    LOG.info("Watching: %s", inbox)
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        obs.stop()
    obs.join()

if __name__ == "__main__":
    main()
