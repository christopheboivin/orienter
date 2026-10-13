from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from eastwalk import db
from eastwalk.app import App
from eastwalk.config import load_config

STATIC = Path(__file__).parent / "static"


class SetupIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    label: str
    start_date: date
    stride_m: float = Field(gt=0.2, lt=2.0)


class StrideIn(BaseModel):
    stride_m: float = Field(gt=0.2, lt=2.0)


class DecisionIn(BaseModel):
    choice: str
    lat: float | None = None
    lon: float | None = None
    label: str | None = None


def create_app(app_state: App | None = None) -> FastAPI:
    core = app_state or App(load_config())
    api = FastAPI(title="eastwalk")

    @api.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @api.get("/api/state")
    def state():
        with core.lock:
            s = core.game.state()
        s["sync"] = {
            "busy": core.status.busy,
            "message": core.status.message,
            "errors": core.status.errors,
        }
        s["problems"] = core.problems()
        return s

    @api.post("/api/setup")
    def setup(body: SetupIn):
        if body.start_date > date.today():
            raise HTTPException(400, "Start date can't be in the future.")
        with core.lock:
            core.game.setup(body.lat, body.lon, body.label, body.start_date, body.stride_m)
        core.sync_in_background(force=True)
        return {"ok": True}

    @api.post("/api/stride")
    def stride(body: StrideIn):
        with core.lock:
            core.game.set_stride(body.stride_m)
        core.sync_in_background()
        return {"ok": True}

    @api.post("/api/sync")
    def sync(force: bool = False):
        return {"started": core.sync_in_background(force=force)}

    @api.post("/api/decision")
    def decision(body: DecisionIn):
        dest = (body.lat, body.lon) if body.lat is not None and body.lon is not None else None
        with core.lock:
            try:
                core.game.resolve_decision(body.choice, dest, body.label)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
        core.sync_in_background(force=False)
        return {"ok": True}

    @api.get("/api/geocode")
    def geocode(q: str):
        return core.places.geocode(q)

    @api.get("/api/photo")
    def photo(lat: float, lon: float):
        # ~100 m buckets so the photo doesn't change on every refresh
        key = f"photo:{lat:.3f},{lon:.3f}"
        with core.lock:
            cached = db.cache_get(core.conn, key)
        if cached is not None:
            return cached
        found = core.photos.find(lat, lon) or {}
        with core.lock:
            db.cache_put(core.conn, key, found)
        return found

    api.mount("/static", StaticFiles(directory=STATIC), name="static")
    return api
