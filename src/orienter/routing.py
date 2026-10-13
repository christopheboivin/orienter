"""Walking routes from openrouteservice (foot-walking profile: no motorways, prefers
roads, sidewalks and paths)."""

from typing import Protocol

import httpx

from orienter.geo import Point
from orienter.net import Throttle

ORS_URL = "https://api.openrouteservice.org"
PROFILE = "foot-walking"


class RoutingError(Exception):
    pass


class Router(Protocol):
    def snap(self, points: list[Point], radius_m: float) -> list[Point | None]:
        """Nearest routable point for each input, or None if nothing within radius."""
        ...

    def route(self, start: Point, end: Point) -> list[Point] | None:
        """Walking path from start to end, or None if there is no route."""
        ...


class OrsRouter:
    def __init__(self, api_key: str, http: httpx.Client):
        self.http = http
        self.headers = {"Authorization": api_key}
        self.throttle = Throttle(1.6)  # free tier: 40 requests/minute

    def _post(self, path: str, body: dict) -> httpx.Response:
        self.throttle.wait()
        return self.http.post(f"{ORS_URL}{path}", json=body, headers=self.headers)

    def snap(self, points: list[Point], radius_m: float) -> list[Point | None]:
        resp = self._post(
            f"/v2/snap/{PROFILE}/json",
            {"locations": [[lon, lat] for lat, lon in points], "radius": radius_m},
        )
        if resp.status_code != 200:
            raise RoutingError(f"ORS snap failed ({resp.status_code}): {resp.text[:300]}")
        out: list[Point | None] = []
        for loc in resp.json().get("locations", []):
            out.append((loc["location"][1], loc["location"][0]) if loc else None)
        return out

    def route(self, start: Point, end: Point) -> list[Point] | None:
        resp = self._post(
            f"/v2/directions/{PROFILE}/geojson",
            {
                "coordinates": [[start[1], start[0]], [end[1], end[0]]],
                "radiuses": [1000, 1000],
                "instructions": False,
            },
        )
        if resp.status_code == 404 or (
            resp.status_code == 400 and '"code":20' in resp.text.replace(" ", "")
        ):
            # 20xx: point not found / route not found / unreachable
            return None
        if resp.status_code != 200:
            raise RoutingError(f"ORS route failed ({resp.status_code}): {resp.text[:300]}")
        coords = resp.json()["features"][0]["geometry"]["coordinates"]
        return [(lat, lon) for lon, lat, *_ in coords]
