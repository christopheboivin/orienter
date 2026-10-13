"""Which city / region / country a route passes through (OSM admin boundaries via
Overpass), with a Wikipedia summary as trivia."""

import logging
import time
from urllib.parse import quote

import httpx

from orienter.geo import Point
from orienter.net import Throttle

log = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org"
KIND_BY_ADMIN_LEVEL = {"2": "country", "4": "region", "8": "city"}


class ServiceBusy(Exception):
    """A free API refused or timed out: try again later."""


class PlaceFinder:
    def __init__(self, http: httpx.Client, wiki_lang: str):
        self.http = http
        self.wiki_lang = wiki_lang
        self.overpass_throttle = Throttle(2.0)
        self.nominatim_throttle = Throttle(1.1)  # Nominatim policy: max 1 request/second
        self.wiki_throttle = Throttle(1.0)  # Wikimedia throttles bursts from unknown clients

    def admin_areas(self, points: list[Point]) -> list[list[dict]]:
        """For each point, the admin areas (country/region/city) containing it."""
        parts = ["[out:json][timeout:120];"]
        levels = "|".join(KIND_BY_ADMIN_LEVEL)
        for i, (lat, lon) in enumerate(points):
            parts.append(
                f"is_in({lat:.6f},{lon:.6f})->.p{i};"
                f'area.p{i}[boundary=administrative][admin_level~"^({levels})$"];out tags;'
                f'make sep idx="{i}";out;'
            )
        query = "\n".join(parts)
        for attempt in range(3):
            self.overpass_throttle.wait()
            try:
                resp = self.http.post(OVERPASS_URL, data={"data": query}, timeout=180)
            except httpx.HTTPError as e:
                raise ServiceBusy(f"Overpass server unreachable: {e}") from e
            if resp.status_code == 200:
                break
            if resp.status_code in (429, 504) and attempt < 2:
                time.sleep(15 * (attempt + 1))
                continue
            raise ServiceBusy(f"Overpass server busy (HTTP {resp.status_code})")

        result: list[list[dict]] = []
        current: list[dict] = []
        for el in resp.json().get("elements", []):
            if el["type"] == "sep":
                result.append(current)
                current = []
            else:
                current.append(el)
        return result

    def describe(self, area: dict) -> dict | None:
        tags = area.get("tags", {})
        kind = KIND_BY_ADMIN_LEVEL.get(tags.get("admin_level", ""))
        name = tags.get(f"name:{self.wiki_lang}") or tags.get("name")
        if not kind or not name:
            return None
        return {
            "kind": kind,
            "osm_id": str(area["id"]),
            "name": name,
            "wikipedia": tags.get("wikipedia"),
            "wikidata": tags.get("wikidata"),
        }

    def _wiki_get(self, url: str, params: dict | None = None) -> httpx.Response:
        self.wiki_throttle.wait()
        try:
            resp = self.http.get(url, params=params)
        except httpx.HTTPError as e:
            raise ServiceBusy(f"Wikipedia unreachable: {e}") from e
        if resp.status_code in (429, 503):
            raise ServiceBusy(f"Wikipedia is rate limiting us (HTTP {resp.status_code})")
        return resp

    def wiki_summary(self, wikidata: str | None, wikipedia: str | None) -> dict:
        """Summary of the place's Wikipedia article, preferring the configured language.

        Returns summary None when there is no article; raises ServiceBusy when Wikipedia
        can't answer right now (so the caller can try again later).
        """
        none = {"summary": None, "thumbnail": None, "url": None}
        lang, title = None, None
        if wikidata:
            resp = self._wiki_get(
                "https://www.wikidata.org/w/api.php",
                {
                    "action": "wbgetentities",
                    "ids": wikidata,
                    "props": "sitelinks",
                    "sitefilter": f"{self.wiki_lang}wiki|enwiki",
                    "format": "json",
                },
            )
            try:
                links = resp.json()["entities"][wikidata].get("sitelinks", {})
            except (KeyError, ValueError) as e:
                raise ServiceBusy(f"Unexpected Wikidata answer for {wikidata}") from e
            for code in (self.wiki_lang, "en"):
                if f"{code}wiki" in links:
                    lang, title = code, links[f"{code}wiki"]["title"]
                    break
        if not title and wikipedia and ":" in wikipedia:
            lang, title = wikipedia.split(":", 1)
        if not title:
            return none
        resp = self._wiki_get(
            f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/"
            + quote(title.replace(" ", "_"), safe="")
        )
        if resp.status_code == 404:
            return none
        if resp.status_code != 200:
            raise ServiceBusy(f"Wikipedia answered HTTP {resp.status_code}")
        data = resp.json()
        return {
            "summary": data.get("extract"),
            "thumbnail": (data.get("thumbnail") or {}).get("source"),
            "url": data.get("content_urls", {}).get("desktop", {}).get("page"),
        }

    def reverse(self, lat: float, lon: float) -> dict:
        """Locality, region and country at a point."""
        self.nominatim_throttle.wait()
        resp = self.http.get(
            f"{NOMINATIM_URL}/reverse",
            params={
                "lat": lat,
                "lon": lon,
                "format": "jsonv2",
                "zoom": 12,
                "accept-language": f"{self.wiki_lang},en",
            },
        )
        resp.raise_for_status()
        addr = resp.json().get("address", {})
        locality = next(
            (
                addr[k]
                for k in ("city", "town", "village", "municipality", "hamlet", "suburb", "county")
                if k in addr
            ),
            None,
        )
        return {"city": locality, "region": addr.get("state"), "country": addr.get("country")}

    def geocode(self, query: str) -> list[dict]:
        self.nominatim_throttle.wait()
        resp = self.http.get(
            f"{NOMINATIM_URL}/search", params={"q": query, "format": "jsonv2", "limit": 6}
        )
        resp.raise_for_status()
        return [
            {"label": r["display_name"], "lat": float(r["lat"]), "lon": float(r["lon"])}
            for r in resp.json()
        ]
