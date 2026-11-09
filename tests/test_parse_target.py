import pytest

from star_report.resolve import parse_target

CASES = [  # input, kind, value, query_name, planet
    ("TIC 420814525", "tic", "420814525", "TIC 420814525", None),
    ("TIC420814525", "tic", "420814525", "TIC 420814525", None),
    ("tic 420814525", "tic", "420814525", "TIC 420814525", None),
    ("Gaia DR3 1779546757669063552", "gaia_dr3", "1779546757669063552", "Gaia DR3 1779546757669063552", None),
    ("Gaia DR2 1779546757669063552", "gaia_dr2", "1779546757669063552", "Gaia DR2 1779546757669063552", None),
    ("TOI-700", "toi", "700", "TOI-700", None),
    ("TOI 700.01", "toi", "700", "TOI-700", ".01"),
    ("toi700", "toi", "700", "TOI-700", None),
    ("TOI-700 d", "toi", "700", "TOI-700", "d"),
    ("KOI-72", "koi", "72", "KOI-72", None),
    ("K00072.01", "koi", "72", "KOI-72", ".01"),
    ("KIC 11904151", "kic", "11904151", "KIC 11904151", None),
    ("EPIC 201367065", "epic", "201367065", "EPIC 201367065", None),
    ("HD 209458", "hd", "209458", "HD 209458", None),
    ("hd209458", "hd", "209458", "HD 209458", None),
    ("HD 209458b", "hd", "209458", "HD 209458", "b"),
    ("HD 209458B", "hd", "209458B", "HD 209458B", None),  # uppercase = stellar component, kept
    ("HIP 108859", "hip", "108859", "HIP 108859", None),
    ("HR 8781", "hr", "8781", "HR 8781", None),
    ("GJ 699", "gj", "699", "GJ 699", None),
    ("Gl 699", "gj", "699", "GJ 699", None),
    ("2MASS J22031077+1853036", "2mass", "J22031077+1853036", "2MASS J22031077+1853036", None),
    ("WASP-33 b", "name", "WASP-33", "WASP-33", "b"),
    ("Kepler-10c", "name", "Kepler-10", "Kepler-10", "c"),
    ("Barnard’s Star", "name", "Barnard's Star", "Barnard's Star", None),  # curly apostrophe normalized
    ("  HD   209458  ", "hd", "209458", "HD 209458", None),
]


@pytest.mark.parametrize("text,kind,value,query_name,planet", CASES)
def test_parse_target(text, kind, value, query_name, planet):
    p = parse_target(text)
    assert (p.kind, p.value, p.query_name, p.planet) == (kind, value, query_name, planet)
    assert p.raw == text
    assert p.coords is None


def test_gliese_maps_to_gj():
    p = parse_target("Gliese 699")
    assert (p.kind, p.query_name) == ("gj", "GJ 699")


def test_coordinates_are_parsed():
    for text in ["330.7950 18.8843", "330.7950, +18.8843", "22:03:10.77 +18:53:03.5",
                 "22 03 10.77 +18 53 03.5", "22h03m10.77s +18d53m03.5s"]:
        p = parse_target(text)
        assert p.kind == "coords" and abs(p.coords[0] - 330.7949) < 1e-3 and abs(p.coords[1] - 18.8843) < 1e-3


def test_negative_declination():
    assert parse_target("06 45 08.9 -16 42 58").coords[1] < 0
