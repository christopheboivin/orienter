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
        self.lock = threading.RLock()  # one sqlite connection, shared by all requests
        self.status = SyncStatus()

    def problems(self) -> list[str]:
        out = []
        if not self.cfg.ors_api_key:
            out.append("ORS_API_KEY is missing in .env: the route can't be planned.")
        if not self.source.is_logged_in:
            out.append("Not logged in to Garmin: run `uv run orienter garmin-login`.")
        return out

    def sync(self, force: bool = False) -> SyncStatus:
        """Fetch steps then extend the route. Runs in the caller's thread."""
        self.status = SyncStatus(busy=True, message="Fetching steps")
        try:
            with self.lock:
                if self.source.is_logged_in:
                    try:
                        days = self.game.sync_steps(force=force)
                        log.info("synced %d days of steps", days)
                    except Exception as e:  # keep going: the route can still be extended
                        log.exception("step sync failed")
                        self.status.errors.append(f"Step sync failed: {e}")
                if self.game.router is not None:
                    self.game.advance(progress=self._progress)
        except Exception as e:
            log.exception("sync failed")
            self.status.errors.append(str(e))
        finally:
            self.status.busy = False
            self.status.message = ""
        return self.status

    def sync_in_background(self, force: bool = False) -> bool:
        if self.status.busy:
            return False
        self.status.busy = True
        threading.Thread(target=self.sync, args=(force,), daemon=True).start()
        return True

    def _progress(self, message: str) -> None:
        self.status.message = message
