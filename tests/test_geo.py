import pytest

from eastwalk.geo import (
    destination,
    east_displacement_m,
    haversine_m,
    path_length_m,
    point_along,
    sample_every,
    simplify,
    split_path,
)


def test_haversine_one_degree_at_equator():
    assert haversine_m((0, 0), (0, 1)) == pytest.approx(111_195, rel=1e-3)


def test_destination_east_round_trip():
    p = destination((45.0, -73.0), 90, 10_000)
    assert haversine_m((45.0, -73.0), p) == pytest.approx(10_000, rel=1e-6)
    assert east_displacement_m((45.0, -73.0), p) == pytest.approx(10_000, rel=1e-3)


def test_east_displacement_across_antimeridian():
    assert east_displacement_m((0, 179.9), (0, -179.9)) > 0


def test_point_along_and_split():
    path = [(0.0, 0.0), (0.0, 1.0), (0.0, 2.0)]
    total = path_length_m(path)
    mid, seg = point_along(path, total * 0.75)
    assert seg == 1
    assert mid[1] == pytest.approx(1.5)
    before, after = split_path(path, total / 2)
    assert path_length_m(before) + path_length_m(after) == pytest.approx(total)
    assert point_along(path, total * 2)[0] == path[-1]


def test_sample_every_includes_ends():
    path = [(0.0, 0.0), (0.0, 0.1)]  # ~11.1 km
    samples = sample_every(path, 1_000)
    assert samples[0] == (0.0, path[0])
    assert samples[-1][1] == path[-1]
    assert len(samples) == 12


def test_simplify_keeps_corners_drops_collinear():
    path = [(0.0, i / 100) for i in range(11)] + [(0.1, 0.1)]
    assert simplify(path, 5) == [(0.0, 0.0), (0.0, 0.1), (0.1, 0.1)]
