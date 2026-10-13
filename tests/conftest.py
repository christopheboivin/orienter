from datetime import date, timedelta

import pytest

from eastwalk import db
from eastwalk.game import Game
from eastwalk.geo import Point


class FakeRouter:
    """Flat land west of `coast_lon`, sea east of it. Routes are straight lines."""

    def __init__(self, coast_lon: float = 10.0):
        self.coast_lon = coast_lon
        self.route_calls = 0

    def snap(self, points: list[Point], radius_m: float) -> list[Point | None]:
        return [p if p[1] <= self.coast_lon else None for p in points]

    def route(self, start: Point, end: Point) -> list[Point] | None:
        self.route_calls += 1
        n = 20
        return [
            (start[0] + (end[0] - start[0]) * i / n, start[1] + (end[1] - start[1]) * i / n)
            for i in range(n + 1)
        ]


class FakeSource:
    name = "fake"

    def __init__(self, per_day: int):
        self.per_day = per_day

    def daily_steps(self, start: date, end: date) -> dict[date, int]:
        return {start + timedelta(days=i): self.per_day for i in range((end - start).days + 1)}


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def make_game(conn):
    def _make(per_day=10_000, coast_lon=10.0, days=10):
        game = Game(conn, FakeSource(per_day), FakeRouter(coast_lon), places=None)
        game.setup(0.0, 0.0, "Home", date(2026, 1, 1), stride_m=1.0)
        game.sync_steps(today=date(2026, 1, days), force=True)
        return game

    return _make
