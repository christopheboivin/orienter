"""Game state: steps -> walked distance -> position along the planned journey."""

import json
import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from eastwalk import db
from eastwalk.geo import (
    Point,
    haversine_m,
    path_length_m,
    point_along,
    sample_every,
    simplify,
    split_path,
)
from eastwalk.places import PlaceFinder
from eastwalk.planner import EAST, HEADINGS, plan_leg
from eastwalk.routing import Router
from eastwalk.sources import StepSource

log = logging.getLogger(__name__)

LOOKAHEAD_M = 3_000  # keep the route planned a bit beyond the current position
MAX_LEGS_PER_RUN = 60  # ~2,400 km; caps API usage of one sync (a long backfill continues next sync)
PLACE_SAMPLE_M = 1_000
RESYNC_AFTER = timedelta(hours=1)
REFETCH_DAYS = 2  # the last days' counts are still moving when we fetch them
DEFAULT_STRIDE_M = 0.75

STEP_MILESTONES = [10_000, 100_000, 250_000, 500_000] + [n * 1_000_000 for n in range(1, 201)]
KM_MILESTONES = [1, 10, 42.195, 50] + list(range(100, 40_001, 100))

TRANSPORT_MODES = ("ferry", "flight")


class Game:
    def __init__(
        self,
        conn: sqlite3.Connection,
        source: StepSource | None,
        router: Router | None,
        places: PlaceFinder | None,
    ):
        self.conn = conn
        self.source = source
        self.router = router
        self.places = places
        self._display_cache: dict[int, list[Point]] = {}

    # ---- settings -------------------------------------------------------------------

    def get(self, key: str, default=None):
        return db.get_setting(self.conn, key, default)

    @property
    def configured(self) -> bool:
        return self.get("home") is not None and self.get("start_date") is not None

    @property
    def home(self) -> Point:
        h = self.get("home")
        return h["lat"], h["lon"]

    @property
    def stride_m(self) -> float:
        return float(self.get("stride_m", DEFAULT_STRIDE_M))

    def setup(self, lat: float, lon: float, label: str, start_date: date, stride_m: float) -> None:
        """First-run configuration. Changing home or start date restarts the journey."""
        old_home, old_start = self.get("home"), self.get("start_date")
        restart = old_home is not None and (
            (old_home["lat"], old_home["lon"]) != (lat, lon) or old_start != start_date.isoformat()
        )
        with self.conn:
            db.set_setting(self.conn, "home", {"lat": lat, "lon": lon, "label": label})
            db.set_setting(self.conn, "start_date", start_date.isoformat())
            db.set_setting(self.conn, "stride_m", stride_m)
            if restart:
                self._reset_journey()

    def set_stride(self, stride_m: float) -> None:
        with self.conn:
            db.set_setting(self.conn, "stride_m", stride_m)

    def _reset_journey(self) -> None:
        for table in ("legs", "places", "decisions"):
            self.conn.execute(f"DELETE FROM {table}")
        db.set_setting(self.conn, "heading", EAST)
        db.set_setting(self.conn, "ended", False)
        self._display_cache.clear()

    # ---- steps ----------------------------------------------------------------------

    def sync_steps(self, today: date | None = None, force: bool = False) -> int:
        """Fetch new daily step counts. Returns the number of days stored."""
        if self.source is None:
            raise RuntimeError("No step source configured.")
        last_sync = self.get("last_sync")
        if (
            not force
            and last_sync
            and datetime.now(UTC) - datetime.fromisoformat(last_sync) < RESYNC_AFTER
        ):
            return 0
        today = today or date.today()
        start = date.fromisoformat(self.get("start_date"))
        row = self.conn.execute("SELECT MAX(day) AS d FROM daily_steps").fetchone()
        if row["d"]:
            start = max(start, date.fromisoformat(row["d"]) - timedelta(days=REFETCH_DAYS))
        counts = self.source.daily_steps(start, today) if start <= today else {}
        with self.conn:
            for day, steps in counts.items():
                self.conn.execute(
                    "INSERT OR REPLACE INTO daily_steps (day, steps, source, fetched_at) "
                    "VALUES (?, ?, ?, ?)",
                    (day.isoformat(), steps, self.source.name, db.now_iso()),
                )
            db.set_setting(self.conn, "last_sync", db.now_iso())
        return len(counts)

    def total_steps(self) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(steps), 0) AS s FROM daily_steps WHERE day >= ?",
            (self.get("start_date"),),
        ).fetchone()
        return int(row["s"])

    def walked_m(self) -> float:
        return self.total_steps() * self.stride_m

    # ---- journey --------------------------------------------------------------------

    def legs(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM legs ORDER BY seq").fetchall()

    def _last_leg(self) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM legs ORDER BY seq DESC LIMIT 1").fetchone()

    def planned_end_m(self) -> float:
        leg = self._last_leg()
        return leg["walk_start_m"] + leg["walk_m"] if leg else 0.0

    def route_end(self) -> Point:
        leg = self._last_leg()
        return tuple(json.loads(leg["geometry"])[-1]) if leg else self.home

    def pending_decision(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM decisions WHERE resolved_at IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def _add_leg(self, mode: str, path: list[Point], walk_m: float, label: str | None = None):
        seq = (self._last_leg() or {"seq": 0})["seq"] + 1
        self.conn.execute(
            "INSERT INTO legs (seq, mode, geometry, length_m, walk_start_m, walk_m, label, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                seq,
                mode,
                json.dumps([[round(lat, 6), round(lon, 6)] for lat, lon in path]),
                path_length_m(path),
                self.planned_end_m(),
                walk_m,
                label,
                db.now_iso(),
            ),
        )

    def advance(
        self, max_legs: int = MAX_LEGS_PER_RUN, progress: Callable[[str], None] = lambda _: None
    ) -> int:
        """Plan legs until the route covers the walked distance. Returns legs planned."""
        if self.router is None:
            raise RuntimeError("No router configured (set ORS_API_KEY in .env).")
        target = self.walked_m() + LOOKAHEAD_M
        planned = 0
        while (
            self.planned_end_m() < target
            and planned < max_legs
            and not self.get("ended", False)
            and self.pending_decision() is None
        ):
            progress(f"Planning route: {self.planned_end_m() / 1000:.0f} / {target / 1000:.0f} km")
            start = self.route_end()
            result = plan_leg(self.router, start, self.get("heading", EAST))
            with self.conn:
                if result is None:
                    self.conn.execute(
                        "INSERT INTO decisions (created_at, lat, lon, walk_m, reason) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            db.now_iso(),
                            start[0],
                            start[1],
                            self.planned_end_m(),
                            "No walkable way onward from here: probably a coastline.",
                        ),
                    )
                    break
                path, heading = result
                self._add_leg("walk", path, path_length_m(path))
                db.set_setting(self.conn, "heading", heading)
            planned += 1
        self.detect_places(progress)
        return planned

    def detect_places(self, progress: Callable[[str], None] = lambda _: None) -> None:
        """Find the cities/regions/countries crossed by legs not yet analysed."""
        if self.places is None:
            return
        todo = self.conn.execute(
            "SELECT * FROM legs WHERE places_done = 0 AND mode = 'walk' ORDER BY seq"
        ).fetchall()
        for i, leg in enumerate(todo, 1):
            progress(f"Looking up places along the route ({i}/{len(todo)})")
            path = [tuple(p) for p in json.loads(leg["geometry"])]
            samples = sample_every(path, PLACE_SAMPLE_M)
            try:
                areas = self.places.admin_areas([p for _, p in samples])
            except Exception as e:  # places are a bonus; retry next sync
                log.warning("place lookup failed for leg %s: %s", leg["seq"], e)
                return
            new_places = []
            with self.conn:
                for (dist, point), found in zip(samples, areas, strict=False):
                    for area in found:
                        place = self.places.describe(area)
                        if not place:
                            continue
                        cur = self.conn.execute(
                            "INSERT OR IGNORE INTO places (kind, osm_id, name, at_walk_m, lat, "
                            "lon, wikipedia, wikidata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                place["kind"],
                                place["osm_id"],
                                place["name"],
                                leg["walk_start_m"] + dist,
                                point[0],
                                point[1],
                                place["wikipedia"],
                                place["wikidata"],
                            ),
                        )
                        if cur.rowcount:
                            new_places.append((cur.lastrowid, place))
                self.conn.execute("UPDATE legs SET places_done = 1 WHERE seq = ?", (leg["seq"],))
            for place_id, place in new_places:
                info = self.places.wiki_summary(place["wikidata"], place["wikipedia"])
                if info:
                    with self.conn:
                        self.conn.execute(
                            "UPDATE places SET summary = ?, thumbnail = ?, url = ? WHERE id = ?",
                            (info["summary"], info["thumbnail"], info["url"], place_id),
                        )

    def resolve_decision(
        self, choice: str, dest: Point | None = None, label: str | None = None
    ) -> None:
        decision = self.pending_decision()
        if decision is None:
            raise ValueError("No decision is pending.")
        with self.conn:
            if choice in HEADINGS:
                db.set_setting(self.conn, "heading", choice)
            elif choice in TRANSPORT_MODES:
                if dest is None:
                    raise ValueError(f"A {choice} needs a destination.")
                self._add_leg(choice, [self.route_end(), dest], 0.0, label)
                self.conn.execute(
                    "UPDATE legs SET places_done = 1 WHERE seq = (SELECT MAX(seq) FROM legs)"
                )
                db.set_setting(self.conn, "heading", EAST)
            elif choice == "end":
                db.set_setting(self.conn, "ended", True)
            else:
                raise ValueError(f"Unknown choice: {choice}")
            self.conn.execute(
                "UPDATE decisions SET choice = ?, resolved_at = ? WHERE id = ?",
                (choice if not label else f"{choice}: {label}", db.now_iso(), decision["id"]),
            )

    # ---- position -------------------------------------------------------------------

    def position(self, walked_m: float) -> tuple[Point, int | None]:
        """Where you are after walking `walked_m`, and the leg you're on."""
        legs = self.legs()
        for leg in legs:
            if leg["walk_m"] > 0 and walked_m < leg["walk_start_m"] + leg["walk_m"]:
                path = [tuple(p) for p in json.loads(leg["geometry"])]
                point, _ = point_along(path, walked_m - leg["walk_start_m"])
                return point, leg["seq"]
        if legs:
            return tuple(json.loads(legs[-1]["geometry"])[-1]), legs[-1]["seq"]
        return self.home, None

    def _display_path(self, leg: sqlite3.Row) -> list[Point]:
        if leg["seq"] not in self._display_cache:
            path = [tuple(p) for p in json.loads(leg["geometry"])]
            self._display_cache[leg["seq"]] = simplify(path, 10) if leg["mode"] == "walk" else path
        return self._display_cache[leg["seq"]]

    def paths(self, walked_m: float) -> tuple[list[dict], list[dict]]:
        """Display polylines: (already walked, planned ahead)."""
        done, ahead = [], []
        for leg in self.legs():
            end = leg["walk_start_m"] + leg["walk_m"]
            path = self._display_path(leg)
            if leg["walk_m"] == 0:
                (done if walked_m >= leg["walk_start_m"] else ahead).append(
                    {"mode": leg["mode"], "path": path, "label": leg["label"]}
                )
            elif walked_m >= end:
                done.append({"mode": leg["mode"], "path": path})
            elif walked_m <= leg["walk_start_m"]:
                ahead.append({"mode": leg["mode"], "path": path})
            else:
                full = [tuple(p) for p in json.loads(leg["geometry"])]
                before, after = split_path(full, walked_m - leg["walk_start_m"])
                done.append({"mode": "walk", "path": simplify(before, 10)})
                ahead.append({"mode": "walk", "path": simplify(after, 10)})
        return done, ahead

    # ---- summary for the UI ---------------------------------------------------------

    def state(self) -> dict:
        if not self.configured:
            return {"configured": False}
        steps = self.total_steps()
        walked = steps * self.stride_m
        (lat, lon), _ = self.position(walked)
        done, ahead = self.paths(walked)
        days = self.conn.execute(
            "SELECT day, steps FROM daily_steps WHERE day >= ? ORDER BY day",
            (self.get("start_date"),),
        ).fetchall()

        places = [
            dict(r)
            for r in self.conn.execute(
                "SELECT kind, name, at_walk_m, lat, lon, summary, thumbnail, url FROM places "
                "WHERE at_walk_m <= ? ORDER BY at_walk_m, CASE kind WHEN 'country' THEN 0 "
                "WHEN 'region' THEN 1 ELSE 2 END",
                (walked,),
            )
        ]
        current = {}
        for p in places:
            current[p["kind"]] = p["name"]

        decision = self.pending_decision()
        planned_end = self.planned_end_m()
        return {
            "configured": True,
            "ended": self.get("ended", False),
            "home": self.get("home"),
            "start_date": self.get("start_date"),
            "stride_m": self.stride_m,
            "heading": self.get("heading", EAST),
            "last_sync": self.get("last_sync"),
            "stats": {
                "steps": steps,
                "walked_m": walked,
                "days": len(days),
                "avg_steps": round(steps / len(days)) if days else 0,
                "last_day": dict(days[-1]) if days else None,
                "straight_line_m": haversine_m(self.home, (lat, lon)),
                "planned_m": planned_end,
                # steps walked beyond the planned route while waiting for a decision
                "banked_m": max(0.0, walked - planned_end) if decision else 0.0,
            },
            "daily": [dict(d) for d in days[-60:]],
            "position": {"lat": lat, "lon": lon},
            "walked": done,
            "ahead": ahead,
            "current_place": current,
            "places": places,
            "milestones": milestones(steps, walked),
            "decision": dict(decision) if decision else None,
        }


def milestones(steps: int, walked_m: float) -> dict:
    km = walked_m / 1000
    steps_done = [s for s in STEP_MILESTONES if s <= steps]
    km_done = [k for k in KM_MILESTONES if k <= km]
    next_steps = next((s for s in STEP_MILESTONES if s > steps), None)
    next_km = next((k for k in KM_MILESTONES if k > km), None)
    return {
        "reached_count": len(steps_done) + len(km_done),
        "latest_steps": steps_done[-1] if steps_done else None,
        "latest_km": km_done[-1] if km_done else None,
        "next_steps": next_steps,
        "next_km": next_km,
    }
