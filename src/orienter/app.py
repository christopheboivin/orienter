"""Wires the services together and runs long syncs in a background thread."""

import logging
import threading
from dataclasses import dataclass, field

from orienter import db
from orienter.config import Config
from orienter.game import Game
from orienter.net import client
from orienter.photos import PhotoFinder
from orienter.places import PlaceFinder
from orienter.routing import OrsRouter
from orienter.sources.csv_import import parse_steps_csv
from orienter.sources.garmin import GarminStepSource

log = logging.getLogger(__name__)


@dataclass
class SyncStatus:
    busy: bool = False
    message: str = ""
    errors: list[str] = field(default_factory=list)


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.http = client(cfg.user_agent)
        self.conn = db.connect(cfg.db_path)
        self.places = PlaceFinder(self.http, cfg.wiki_lang)
        self.photos = PhotoFinder(self.http, cfg.mapillary_token)
        self.source = GarminStepSource(cfg.garmin_tokenstore)
        router = OrsRouter(cfg.ors_api_key, self.http) if cfg.ors_api_key else None
        self.game = Game(self.conn, self.source, router, self.places)
        self.lock = threading.RLock()  # guards self.conn, used by web requests
        self.status = SyncStatus()
        self._sync_guard = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._rerun = False

    def problems(self) -> list[str]:
        out = []
        if not self.cfg.ors_api_key:
            out.append("ORS_API_KEY is missing in .env: the route can't be planned.")
        has_steps = self.conn.execute("SELECT 1 FROM daily_steps LIMIT 1").fetchone()
        if not self.source.is_logged_in and not has_steps:
            out.append(
                "No steps yet: import a Garmin Connect CSV (Settings, below) "
                "or run `uv run orienter garmin-login`."
            )
        return out

    def import_csv(self, text: str) -> dict:
        """Import a Garmin Connect steps CSV, then extend the route in the background."""
        counts = parse_steps_csv(text)
        with self.lock:
            self.game.import_steps(counts, source="garmin-csv")
        if self.game.configured:
            self.sync_in_background()
        days = sorted(counts)
        return {"days": len(days), "first": days[0].isoformat(), "last": days[-1].isoformat()}

    def sync(self, force: bool = False) -> SyncStatus:
        """Fetch steps then extend the route. Runs in the caller's thread.

        Uses its own database connection and never takes `self.lock`: route planning can
        take minutes and the web page must stay responsive meanwhile.
        """
        self.status = SyncStatus(busy=True, message="Fetching steps")
        game = Game(db.connect(self.cfg.db_path), self.source, self.game.router, self.places)
        try:
            if self.source.is_logged_in:
                try:
                    days = game.sync_steps(force=force)
                    log.info("synced %d days of steps", days)
                except Exception as e:  # keep going: the route can still be extended
                    log.exception("step sync failed")
                    self.status.errors.append(f"Step sync failed: {e}")
            if game.configured and game.router is not None:
                game.advance(progress=self._progress, should_stop=self._cancel.is_set)
        except Exception as e:
            log.exception("sync failed")
            self.status.errors.append(str(e))
        finally:
            game.conn.close()
            self.status.busy = False
            self.status.message = ""
        return self.status

    def sync_in_background(self, force: bool = False) -> bool:
        """Start a background sync; if one is running, run another right after it."""
        with self._sync_guard:
            if self._thread and self._thread.is_alive():
                self._rerun = True
                return False
            self._cancel.clear()
            self.status.busy = True
            self._thread = threading.Thread(target=self._sync_loop, args=(force,), daemon=True)
            self._thread.start()
            return True

    def _sync_loop(self, force: bool) -> None:
        while True:
            self.sync(force)
            with self._sync_guard:
                if not self._rerun or self._cancel.is_set():
                    self._rerun = False
                    return
                self._rerun, force = False, False

    def stop_sync(self, timeout: float = 120) -> None:
        """Ask a running sync to stop after its current step, and wait for it."""
        thread = self._thread
        if thread and thread.is_alive():
            self._cancel.set()
            thread.join(timeout)

    def _progress(self, message: str) -> None:
        self.status.message = message
