"""A photo of where you are: Mapillary street-level imagery if a token is configured,
otherwise the nearest geotagged picture on Wikimedia Commons."""

import logging
import re

import httpx

from orienter.geo import haversine_m

log = logging.getLogger(__name__)


class PhotoFinder:
    def __init__(self, http: httpx.Client, mapillary_token: str | None):
        self.http = http
        self.mapillary_token = mapillary_token

    def find(self, lat: float, lon: float) -> dict | None:
        finders = [self._mapillary] if self.mapillary_token else []
        finders.append(self._commons)
        for finder in finders:
            try:
                photo = finder(lat, lon)
            except (httpx.HTTPError, KeyError, ValueError) as e:
                log.warning("%s failed: %s", finder.__name__, e)
                continue
            if photo:
                photo["distance_m"] = round(haversine_m((lat, lon), (photo["lat"], photo["lon"])))
                return photo
        return None

    def _mapillary(self, lat: float, lon: float) -> dict | None:
        for half in (0.003, 0.02):
            resp = self.http.get(
                "https://graph.mapillary.com/images",
                params={
                    "access_token": self.mapillary_token,
                    "fields": "id,thumb_1024_url,captured_at,creator,computed_geometry,geometry",
                    "bbox": f"{lon - half},{lat - half},{lon + half},{lat + half}",
                    "limit": 20,
                },
            )
            resp.raise_for_status()
            images = [i for i in resp.json().get("data", []) if i.get("thumb_1024_url")]
            if not images:
                continue

            def coords(img: dict) -> tuple[float, float]:
                geom = img.get("computed_geometry") or img["geometry"]
                return geom["coordinates"][1], geom["coordinates"][0]

            img = min(images, key=lambda i: haversine_m((lat, lon), coords(i)))
            plat, plon = coords(img)
            return {
                "source": "Mapillary",
                "url": img["thumb_1024_url"],
                "page_url": f"https://www.mapillary.com/app/?pKey={img['id']}",
                "credit": (img.get("creator") or {}).get("username", "Mapillary contributor"),
                "license": "CC BY-SA 4.0",
                "lat": plat,
                "lon": plon,
            }
        return None

    def _commons(self, lat: float, lon: float) -> dict | None:
        resp = self.http.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "generator": "geosearch",
                "ggscoord": f"{lat}|{lon}",
                "ggsradius": 10_000,
                "ggsnamespace": 6,
                "ggslimit": 30,
                "prop": "imageinfo|coordinates",
                "iiprop": "url|mime|extmetadata",
                "iiurlwidth": 1024,
            },
        )
        resp.raise_for_status()
        candidates = []
        for page in resp.json().get("query", {}).get("pages", {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            coord = (page.get("coordinates") or [None])[0]
            if not coord or info.get("mime") not in ("image/jpeg", "image/png"):
                continue
            candidates.append((haversine_m((lat, lon), (coord["lat"], coord["lon"])), page))
        if not candidates:
            return None
        _, page = min(candidates, key=lambda c: c[0])
        info = page["imageinfo"][0]
        meta = info.get("extmetadata", {})
        artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()
        return {
            "source": "Wikimedia Commons",
            "url": info.get("thumburl") or info["url"],
            "page_url": info.get("descriptionurl"),
            "credit": artist or "Wikimedia Commons contributor",
            "license": meta.get("LicenseShortName", {}).get("value"),
            "lat": page["coordinates"][0]["lat"],
            "lon": page["coordinates"][0]["lon"],
        }
