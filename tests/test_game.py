from datetime import date

import pytest

from orienter.geo import haversine_m
from orienter.planner import COAST_NORTH, EAST


def test_steps_to_distance(make_game):
    game = make_game(per_day=10_000, days=10)
    assert game.total_steps() == 100_000
    assert game.walked_m() == pytest.approx(100_000)


def test_resync_refetches_recent_days_without_double_counting(make_game):
    game = make_game(per_day=10_000, days=10)
    game.sync_steps(today=date(2026, 1, 10), force=True)
    assert game.total_steps() == 100_000


def test_advance_plans_eastward_route_covering_walked_distance(make_game):
    game = make_game(per_day=10_000, days=10)  # 100 km
    game.advance()
    assert game.planned_end_m() >= game.walked_m()
    (lat, lon), _ = game.position(game.walked_m())
    assert lon > 0.8  # ~100 km east of (0, 0)
    assert abs(lat) < 0.5
    assert game.pending_decision() is None


def test_position_is_stable_once_planned(make_game):
    game = make_game(per_day=10_000, days=10)
    game.advance()
    before = game.position(50_000)
    game.advance()
    assert game.position(50_000) == before


def test_coast_raises_decision_and_banks_steps(make_game):
    game = make_game(per_day=50_000, days=10, coast_lon=1.0)  # 500 km, sea after ~111 km
    game.advance()
    decision = game.pending_decision()
    assert decision is not None
    state = game.state()
    assert state["stats"]["banked_m"] > 0
    assert state["position"]["lon"] <= 1.0


def test_flight_crosses_without_using_steps(make_game):
    game = make_game(per_day=50_000, days=10, coast_lon=1.0)
    game.advance()
    planned_before = game.planned_end_m()
    game.router.coast_lon = 50.0  # the destination continent
    game.resolve_decision("flight", (0.0, 20.0), "Somewhere")
    game.advance()
    legs = game.legs()
    flight = next(leg for leg in legs if leg["mode"] == "flight")
    assert flight["walk_m"] == 0
    assert flight["walk_start_m"] == pytest.approx(planned_before)
    (_, lon), _ = game.position(game.walked_m())
    assert lon > 20.0
    assert game.get("heading") == EAST


def test_coast_choice_sets_heading(make_game):
    game = make_game(per_day=50_000, days=10, coast_lon=1.0)
    game.advance()
    game.resolve_decision(COAST_NORTH)
    assert game.get("heading") == COAST_NORTH
    game.advance()
    (lat, _), _ = game.position(game.walked_m())
    assert lat > 0.5


def test_changing_home_restarts_journey(make_game):
    game = make_game()
    game.advance()
    assert game.legs()
    game.setup(1.0, 1.0, "New home", date(2026, 1, 1), 1.0)
    assert not game.legs()
    assert haversine_m(game.position(0)[0], (1.0, 1.0)) < 1


def test_places_carry_the_day_they_were_reached(make_game):
    game = make_game(per_day=10_000, days=10)  # stride 1 m: 10 km/day from 2026-01-01
    for i, at in enumerate([0, 10_000, 10_001, 95_000, 150_000]):
        game.conn.execute(
            "INSERT INTO places (kind, osm_id, name, at_walk_m, lat, lon) "
            "VALUES ('city', ?, ?, ?, 0, 0)",
            (str(i), f"P{i}", at),
        )
    places = game.state()["places"]
    assert [(p["name"], p["reached_on"]) for p in places] == [
        ("P0", "2026-01-01"),
        ("P1", "2026-01-01"),  # exactly 10 km: reached at the end of day 1
        ("P2", "2026-01-02"),
        ("P3", "2026-01-10"),
    ]  # P4 (150 km) is beyond the 100 km walked
    assert "summary" not in places[0]
    assert game.place(places[0]["id"])["name"] == "P0"
