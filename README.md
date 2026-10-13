# Eastwalk

Walk east from home, one real step at a time. Daily steps from your Garmin watch move a
walker along real roads and trails (OpenStreetMap), heading east. Along the way you get the
cities, regions and countries you enter (with Wikipedia trivia) and a photo of where you are.

## How it works

```
Garmin ──► sync ──► SQLite (daily steps, planned legs, places)
                       │
     openrouteservice ◄┤ plans the next ~40 km leg east when needed
     Overpass / Wiki  ◄┤ detects cities/regions/countries crossed
                       ▼
        FastAPI ──► browser (Leaflet map, photo, stats)
```

- **Steps are the source of truth.** Distance = steps × stride length (a setting), so
  changing watch or stride never corrupts history.
- **The route is planned in legs and never re-routed.** The planner aims ~40 km east, snaps
  the target to the walkable network and asks openrouteservice (`foot-walking`: no
  motorways) for a route. Detours above 3× are rejected, so no dead ends or backtracking.
- **When east is blocked** (coastline), the game stops and asks: follow the coast north or
  south, take a ferry, take a flight, or end. Ferries and flights don't use steps; steps
  walked meanwhile are banked.

## Setup

1. Install [uv](https://docs.astral.sh/uv/): `winget install astral-sh.uv`
2. `uv sync`
3. `copy .env.example .env` and set `ORS_API_KEY` (free:
   https://openrouteservice.org/dev/#/signup). Optional: `MAPILLARY_TOKEN`, `WIKI_LANG`.
4. `uv run eastwalk garmin-login` (password is used once, only tokens are stored in `data/`)
5. `uv run eastwalk serve`, then choose your home and start date in the browser.

### Daily update

The app syncs when it starts and when the page asks it to. For a sync even when it's not
running, create a daily Windows Task Scheduler job:

```
schtasks /Create /SC DAILY /ST 06:00 /TN Eastwalk /TR "cmd /c cd /d C:\Users\chris\code\eastwalk && uv run eastwalk sync"
```

## Development

```
uv run pytest
uv run ruff check . && uv run ruff format .
```

Code map (`src/eastwalk/`): `sources/` step providers (Garmin behind a `StepSource`
protocol) · `planner.py` leg planning · `routing.py` openrouteservice · `game.py` steps →
position, decisions, places · `places.py` Overpass + Wikipedia · `photos.py` Mapillary /
Wikimedia Commons · `api.py` FastAPI · `static/` the web page.

## Data & credits

Map data © OpenStreetMap contributors. Routing by openrouteservice. Photos from Mapillary
(CC BY-SA) or Wikimedia Commons (per-image license). Trivia from Wikipedia (CC BY-SA).
