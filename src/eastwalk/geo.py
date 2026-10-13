"""Small spherical-geometry helpers. Points are (lat, lon) tuples in degrees."""

import math

EARTH_RADIUS_M = 6_371_008.8

Point = tuple[float, float]


def haversine_m(a: Point, b: Point) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def wrap_lon(lon: float) -> float:
    return (lon + 180) % 360 - 180


def destination(p: Point, bearing_deg: float, distance_m: float) -> Point:
    lat1, lon1 = map(math.radians, p)
    brg = math.radians(bearing_deg)
    d = distance_m / EARTH_RADIUS_M
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(brg))
    lon2 = lon1 + math.atan2(
        math.sin(brg) * math.sin(d) * math.cos(lat1),
        math.cos(d) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), wrap_lon(math.degrees(lon2))


def east_displacement_m(a: Point, b: Point) -> float:
    """Signed east-west distance from a to b, measured at their mean latitude."""
    dlon = wrap_lon(b[1] - a[1])
    mean_lat = math.radians((a[0] + b[0]) / 2)
    return math.radians(dlon) * EARTH_RADIUS_M * math.cos(mean_lat)


def path_length_m(path: list[Point]) -> float:
    return sum(haversine_m(path[i], path[i + 1]) for i in range(len(path) - 1))


def point_along(path: list[Point], distance_m: float) -> tuple[Point, int]:
    """Point at `distance_m` along `path`, plus the index of the segment it falls in."""
    if distance_m <= 0 or len(path) == 1:
        return path[0], 0
    walked = 0.0
    for i in range(len(path) - 1):
        seg = haversine_m(path[i], path[i + 1])
        if walked + seg >= distance_m:
            t = (distance_m - walked) / seg if seg else 0.0
            a, b = path[i], path[i + 1]
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), i
        walked += seg
    return path[-1], len(path) - 2


def split_path(path: list[Point], distance_m: float) -> tuple[list[Point], list[Point]]:
    """Split a path into the part before and after `distance_m`."""
    p, i = point_along(path, distance_m)
    return [*path[: i + 1], p], [p, *path[i + 1 :]]


def sample_every(path: list[Point], step_m: float) -> list[tuple[float, Point]]:
    """(distance along path, point) roughly every `step_m`, always including both ends."""
    out: list[tuple[float, Point]] = [(0.0, path[0])]
    walked, next_mark = 0.0, step_m
    for i in range(len(path) - 1):
        seg = haversine_m(path[i], path[i + 1])
        while seg and walked + seg >= next_mark:
            t = (next_mark - walked) / seg
            a, b = path[i], path[i + 1]
            out.append((next_mark, (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)))
            next_mark += step_m
        walked += seg
    if walked - out[-1][0] < step_m / 4 and len(out) > 1:
        out.pop()  # too close to the end: let the end point stand in for it
    if walked > out[-1][0]:
        out.append((walked, path[-1]))
    return out


def simplify(path: list[Point], tolerance_m: float) -> list[Point]:
    """Ramer-Douglas-Peucker on an equirectangular projection (fine at display scale)."""
    if len(path) < 3:
        return list(path)
    lat0 = math.radians(path[0][0])
    xy = [
        (math.radians(lon) * EARTH_RADIUS_M * math.cos(lat0), math.radians(lat) * EARTH_RADIUS_M)
        for lat, lon in path
    ]
    keep = [False] * len(path)
    keep[0] = keep[-1] = True
    stack = [(0, len(path) - 1)]
    while stack:
        s, e = stack.pop()
        (x1, y1), (x2, y2) = xy[s], xy[e]
        dx, dy = x2 - x1, y2 - y1
        norm = math.hypot(dx, dy)
        best, best_i = 0.0, -1
        for i in range(s + 1, e):
            x, y = xy[i]
            if norm:
                d = abs(dy * x - dx * y + x2 * y1 - y2 * x1) / norm
            else:
                d = math.hypot(x - x1, y - y1)
            if d > best:
                best, best_i = d, i
        if best > tolerance_m:
            keep[best_i] = True
            stack += [(s, best_i), (best_i, e)]
    return [p for p, k in zip(path, keep, strict=True) if k]
