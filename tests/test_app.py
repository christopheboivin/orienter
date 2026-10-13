import time
from datetime import date, timedelta

from conftest import FakeRouter
from fastapi.testclient import TestClient

from orienter.api import create_app
from orienter.app import App
from orienter.config import Config


class SlowRouter(FakeRouter):
    def route(self, start, end):
        time.sleep(0.3)
        return super().route(start, end)


def make_app(tmp_path) -> App:
    cfg = Config(
        data_dir=tmp_path, ors_api_key=None, mapillary_token=None, wiki_lang="en", contact=None
    )
    core = App(cfg)
    core.game.router = SlowRouter()
    core.places = None
    start = date.today() - timedelta(days=9)
    core.game.import_steps({start + timedelta(days=i): 50_000 for i in range(10)}, "test")
    return core


def test_page_stays_responsive_during_a_long_sync(tmp_path):
    core = make_app(tmp_path)
    client = TestClient(create_app(core))
    home = {"lat": 0.0, "lon": 0.0, "label": "Home", "stride_m": 1.0}
    start = (date.today() - timedelta(days=9)).isoformat()
    assert client.post("/api/setup", json={**home, "start_date": start}).status_code == 200
    assert core.status.busy

    t0 = time.monotonic()
    state = client.get("/api/state").json()
    assert time.monotonic() - t0 < 0.5
    assert state["configured"] and state["sync"]["busy"]

    # Saving the setup again stops the running sync instead of waiting for it to finish.
    t0 = time.monotonic()
    assert client.post("/api/setup", json={**home, "start_date": start}).status_code == 200
    assert time.monotonic() - t0 < 2

    # ...and a new sync picks up from there, its legs visible to the page as they come.
    deadline = time.monotonic() + 5
    while not core.game.legs() and time.monotonic() < deadline:
        time.sleep(0.05)
    core.stop_sync()
    assert not core.status.errors, core.status.errors
    assert core.game.legs()


def test_place_summary_is_fetched_once_and_busy_is_not_cached(tmp_path):
    from orienter.places import ServiceBusy

    class FakePlaces:
        calls = 0
        busy = True

        def wiki_summary(self, wikidata, wikipedia):
            FakePlaces.calls += 1
            if FakePlaces.busy:
                raise ServiceBusy("Wikipedia is rate limiting us (HTTP 429)")
            return {"summary": "A town.", "thumbnail": None, "url": "https://w/x"}

    core = make_app(tmp_path)
    core.places = FakePlaces()
    core.conn.execute(
        "INSERT INTO places (id, kind, osm_id, name, at_walk_m, lat, lon, wikidata) "
        "VALUES (1, 'city', 'a', 'X', 0, 0, 0, 'Q1')"
    )
    assert "error" in core.place(1)  # busy: reported, nothing stored
    FakePlaces.busy = False
    assert core.place(1)["summary"] == "A town."
    assert core.place(1)["summary"] == "A town."
    assert FakePlaces.calls == 2  # the third call came from the database
