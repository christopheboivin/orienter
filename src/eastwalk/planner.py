"""Plans the next leg of the journey.

Instead of walking the road graph intersection by intersection (and backtracking out of
dead ends), we aim at a point some tens of km away in the preferred direction, snap it to
the walkable network and let the router find a path. The router never leads into a dead
end, and a detour cap rejects routes that wander off (e.g. around a huge lake).
"""

import math

from eastwalk.geo import EARTH_RADIUS_M, Point, destination, east_displacement_m, path_length_m
from eastwalk.routing import Router

# Heading modes: walk east, or follow a coast north/south until east is open again.
EAST, COAST_NORTH, COAST_SOUTH = "east", "coast_north", "coast_south"
HEADINGS = (EAST, COAST_NORTH, COAST_SOUTH)

BEARINGS = {
    EAST: [90, 75, 105, 60, 120, 45, 135],
    COAST_NORTH: [45, 20, 0, 340, 315, 70],
    COAST_SOUTH: [135, 160, 180, 200, 225, 110],
}
AXIS = {EAST: (1.0, 0.0), COAST_NORTH: (0.0, 1.0), COAST_SOUTH: (0.0, -1.0)}

LEG_DISTANCES_M = [40_000, 25_000, 12_000, 5_000]
RECHECK_EAST_DISTANCES_M = [25_000, 12_000]  # while following a coast
MIN_GAIN = 0.25  # progress along the axis, as a fraction of the aimed distance
MAX_DETOUR = 3.0  # route length / aimed distance
MAX_ROUTE_TRIES = 3  # per aimed distance, to spare the API quota


def _gain(a: Point, b: Point, heading: str) -> float:
    ax, ay = AXIS[heading]
    north = math.radians(b[0] - a[0]) * EARTH_RADIUS_M
    return ax * east_displacement_m(a, b) + ay * north


def _try(router: Router, start: Point, heading: str, distances: list[int]) -> list[Point] | None:
    for dist in distances:
        targets = [destination(start, b, dist) for b in BEARINGS[heading]]
        snapped = router.snap(targets, radius_m=min(dist / 4, 5_000))
        tries = 0
        for target in snapped:
            if target is None or _gain(start, target, heading) < MIN_GAIN * dist:
                continue
            if tries == MAX_ROUTE_TRIES:
                break
            tries += 1
            path = router.route(start, target)
            if not path or len(path) < 2:
                continue
            if path_length_m(path) > MAX_DETOUR * dist:
                continue
            if _gain(start, path[-1], heading) < MIN_GAIN * dist:
                continue
            if path[0] != start:
                path = [start, *path]
            return path
    return None


def plan_leg(router: Router, start: Point, heading: str) -> tuple[list[Point], str] | None:
    """Next walking leg from `start`, and the heading to keep afterwards.

    Returns None when no acceptable route exists: the player must decide (ferry, flight,
    follow the coast, ...).
    """
    if heading != EAST:
        path = _try(router, start, EAST, RECHECK_EAST_DISTANCES_M)
        if path:
            return path, EAST
    path = _try(router, start, heading, LEG_DISTANCES_M)
    return (path, heading) if path else None
