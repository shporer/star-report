import math

from star_report.coords import parse_coordinates, propagate, separation_arcsec
from star_report.models import Position


def test_barnard_propagates_to_j2000():  # Gaia DR3 epoch 2016.0 -> 2000.0
    pos = Position(ra=269.4486, dec=4.7394, epoch=2016.0, pmra=-801.55, pmdec=10362.39,
                   parallax=546.98, source="gaia_dr3")
    ra, dec = propagate(pos, 2000.0)
    assert abs((dec - 4.7394) * 3600 + 165.8) < 1.0
    assert abs((ra - 269.4486) * 3600 * math.cos(math.radians(4.7394)) - 12.8) < 0.5


def test_no_proper_motion_returns_input():
    pos = Position(ra=10.0, dec=-20.0, epoch=2016.0, source="gaia_dr3")
    assert propagate(pos, 2000.0) == (10.0, -20.0)
    pos = Position(ra=10.0, dec=-20.0, epoch=2016.0, pmra=5.0, source="gaia_dr3")
    assert propagate(pos, 2000.0) == (10.0, -20.0)


def test_missing_parallax_still_propagates():
    pos = Position(ra=10.0, dec=0.0, epoch=2016.0, pmra=1000.0, pmdec=0.0, source="gaia_dr3")
    ra, dec = propagate(pos, 2000.0)
    assert ra < 10.0 and abs(dec) < 1e-6


def test_separation():
    assert abs(separation_arcsec(10.0, 20.0, 10.0, 20.0 + 1 / 3600) - 1.0) < 1e-6


def test_non_coordinates_return_none():
    assert parse_coordinates("HD 209458") is None
    assert parse_coordinates("Kepler-10") is None
    assert parse_coordinates("2MASS J22031077+1853036") is None


def test_coordinates_parse():
    ra, dec = parse_coordinates("330.7950, +18.8843")
    assert (ra, dec) == (330.795, 18.8843)
