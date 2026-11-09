"""Target parsing: turn user input into a typed, SIMBAD-friendly ParsedTarget."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Literal

from star_report.coords import parse_coordinates

IdKind = Literal["tic", "gaia_dr3", "gaia_dr2", "toi", "koi", "kic", "epic", "hd", "hip",
                 "hr", "gj", "2mass", "coords", "name"]


@dataclass(frozen=True)
class ParsedTarget:
    raw: str
    kind: IdKind
    value: str  # the bare number or identifier
    query_name: str  # a SIMBAD-friendly spelling
    coords: tuple[float, float] | None = None
    planet: str | None = None


# (kind, regex, query_name builder); tried in this order, case-insensitively.
_ID_PATTERNS: list[tuple[IdKind, re.Pattern[str], Callable[[str], str]]] = [
    (k, re.compile(rx, re.IGNORECASE), q)
    for k, rx, q in [
        ("tic", r"TIC[\s-]*(\d+)", lambda v: f"TIC {v}"),
        ("gaia_dr3", r"Gaia[\s-]*DR3[\s-]*(\d+)", lambda v: f"Gaia DR3 {v}"),
        ("gaia_dr2", r"Gaia[\s-]*DR2[\s-]*(\d+)", lambda v: f"Gaia DR2 {v}"),
        ("toi", r"TOI[\s-]*(\d+)(?:\.\d+)?", lambda v: f"TOI-{v}"),
        ("koi", r"(?:KOI[\s-]*|K0*)(\d+)(?:\.\d+)?", lambda v: f"KOI-{v}"),
        ("kic", r"KIC[\s-]*(\d+)", lambda v: f"KIC {v}"),
        ("epic", r"EPIC[\s-]*(\d+)", lambda v: f"EPIC {v}"),
        ("hd", r"HD[\s-]*(\d+[A-Z]?)", lambda v: f"HD {v}"),
        ("hip", r"HIP[\s-]*(\d+)", lambda v: f"HIP {v}"),
        ("hr", r"HR[\s-]*(\d+)", lambda v: f"HR {v}"),
        ("gj", r"(?:GJ|Gl|Gliese)[\s-]*(\d+[A-Z]?)", lambda v: f"GJ {v}"),
        ("2mass", r"2MASS[\s-]*(J\d{8}[+-]\d{7})", lambda v: f"2MASS {v}"),
    ]
]

_DOT_PLANET_RE = re.compile(r"^(?P<host>.+?)(?P<planet>\.\d{2})$")
# lowercase letters only: uppercase suffixes are stellar components ("HD 209458B")
_LETTER_PLANET_RE = re.compile(r"^(?P<host>.+?)(?:\s+|(?<=\d))(?P<planet>[b-i])$")


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("’", "'").replace("‘", "'")
    return " ".join(text.split())


def _match_id(text: str) -> tuple[IdKind, str, str] | None:
    for kind, rx, query in _ID_PATTERNS:
        m = rx.fullmatch(text)
        if m:
            value = m.group(1).upper() if kind in ("hd", "gj", "2mass") else m.group(1)
            return kind, value, query(value)
    return None


def parse_target(text: str) -> ParsedTarget:
    norm = _normalize(text)

    planet: str | None = None
    host = norm
    m = _DOT_PLANET_RE.match(norm)
    if m:
        # ".01" style suffix only means a planet candidate for TOI/KOI ids
        base = _match_id(m.group("host"))
        if base and base[0] in ("toi", "koi"):
            host, planet = m.group("host"), m.group("planet")
    else:
        m = _LETTER_PLANET_RE.match(norm)
        if m:
            host, planet = m.group("host"), m.group("planet")

    hit = _match_id(host)
    if hit:
        kind, value, query_name = hit
        return ParsedTarget(raw=text, kind=kind, value=value, query_name=query_name, planet=planet)

    coords = parse_coordinates(norm)
    if coords is not None:
        return ParsedTarget(raw=text, kind="coords", value=norm, query_name=norm, coords=coords)

    return ParsedTarget(raw=text, kind="name", value=host, query_name=host, planet=planet)
