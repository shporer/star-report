"""Target parsing and identity resolution.

parse_target() turns user input into a typed, SIMBAD-friendly ParsedTarget. resolve() asks the
catalog that owns the ID, cross-matches SIMBAD, Gaia DR3 and TIC into one StarIdentity, and
records consistency warnings. The CatalogLookup implementations live here too: LiveLookup
(cached network queries) and RecordingLookup/ReplayLookup (test fixtures).
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

import requests

from star_report.cache import Cache
from star_report.config import Config, ResolveConfig
from star_report.coords import parse_coordinates, propagate, separation_arcsec
from star_report.models import Position, StarIdentity
from star_report.sources import exoplanet_archive
from star_report.sources import gaia as gaia_source
from star_report.sources import simbad as simbad_source
from star_report.sources import tic as tic_source

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


# ---- catalog lookups ----


class CatalogLookup(Protocol):
    """The catalog queries resolve() makes. Every method may raise on network failure."""

    def simbad(self, name: str) -> list[dict]: ...

    def gaia_by_id(self, dr3_id: str) -> dict | None: ...

    def gaia_dr3_from_dr2(self, dr2_id: str) -> list[dict]: ...  # dr2_neighbourhood rows

    def gaia_dr2_from_dr3(self, dr3_id: str) -> list[dict]: ...

    # sorted by dist_arcsec
    def gaia_cone(self, ra: float, dec: float, radius_arcsec: float) -> list[dict]: ...

    def tic_by_id(self, tic: int) -> dict | None: ...

    def tic_by_gaia_dr2(self, dr2_id: str) -> list[dict]: ...

    # sorted by dstArcSec
    def tic_cone(self, ra: float, dec: float, radius_arcsec: float) -> list[dict]: ...

    def toi_host(self, toi: str) -> int | None: ...  # TIC ID

    def koi_host(self, koi: str) -> int | None: ...  # KIC ID


# Each lookup method queries one service; its cache group is named after that service.
_GROUP = {
    "simbad": "simbad",
    "gaia_by_id": "gaia",
    "gaia_dr3_from_dr2": "gaia",
    "gaia_dr2_from_dr3": "gaia",
    "gaia_cone": "gaia",
    "tic_by_id": "tic",
    "tic_by_gaia_dr2": "tic",
    "tic_cone": "tic",
    "toi_host": "exoplanet_archive",
    "koi_host": "exoplanet_archive",
}
_SERVICE = {"simbad": "SIMBAD", "gaia": "Gaia DR3", "tic": "TIC v8.2 (MAST)",
            "exoplanet_archive": "NASA Exoplanet Archive"}
_SINGLE_RECORD = frozenset({"gaia_by_id", "tic_by_id", "toi_host", "koi_host"})


def _nothing(method: str) -> Any:
    """What `method` answers when it finds nothing: None or an empty list."""
    return None if method in _SINGLE_RECORD else []


def _describe(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return (f"{type(exc).__name__}: {text}" if text else type(exc).__name__)[:300]


def _call_key(method: str, args: dict[str, Any]) -> str:
    return f"{method}:{json.dumps(args, sort_keys=True)}"


class _RoutedLookup:
    """Implements each CatalogLookup method as self._call(method, its keyword arguments)."""

    def _call(self, method: str, args: dict[str, Any]) -> Any:
        raise NotImplementedError

    def simbad(self, name: str) -> list[dict]:
        return self._call("simbad", {"name": name})

    def gaia_by_id(self, dr3_id: str) -> dict | None:
        return self._call("gaia_by_id", {"dr3_id": dr3_id})

    def gaia_dr3_from_dr2(self, dr2_id: str) -> list[dict]:
        return self._call("gaia_dr3_from_dr2", {"dr2_id": dr2_id})

    def gaia_dr2_from_dr3(self, dr3_id: str) -> list[dict]:
        return self._call("gaia_dr2_from_dr3", {"dr3_id": dr3_id})

    def gaia_cone(self, ra: float, dec: float, radius_arcsec: float) -> list[dict]:
        return self._call("gaia_cone", {"ra": ra, "dec": dec, "radius_arcsec": radius_arcsec})

    def tic_by_id(self, tic: int) -> dict | None:
        return self._call("tic_by_id", {"tic": tic})

    def tic_by_gaia_dr2(self, dr2_id: str) -> list[dict]:
        return self._call("tic_by_gaia_dr2", {"dr2_id": dr2_id})

    def tic_cone(self, ra: float, dec: float, radius_arcsec: float) -> list[dict]:
        return self._call("tic_cone", {"ra": ra, "dec": dec, "radius_arcsec": radius_arcsec})

    def toi_host(self, toi: str) -> int | None:
        return self._call("toi_host", {"toi": toi})

    def koi_host(self, koi: str) -> int | None:
        return self._call("koi_host", {"koi": koi})


class LiveLookup(_RoutedLookup):
    """Queries the services through the cache: group per service, name "lookup.<method>"."""

    def __init__(self, cache: Cache, session: requests.Session, cfg: Config) -> None:
        self.cache = cache
        self.session = session
        self.cfg = cfg

    def _call(self, method: str, args: dict[str, Any]) -> Any:
        produce = self._queries(args)[method]
        return self.cache.fetch(_GROUP[method], f"lookup.{method}", args, produce).payload

    def _queries(self, a: dict[str, Any]) -> dict[str, Callable[[], Any]]:
        session, timeout = self.session, self.cfg.sources.timeout_s
        return {
            "simbad": lambda: simbad_source.lookup(a["name"], session, self.cfg),
            "gaia_by_id": lambda: gaia_source.by_id(a["dr3_id"], session),
            "gaia_dr3_from_dr2": lambda: gaia_source.dr3_from_dr2(a["dr2_id"], session),
            "gaia_dr2_from_dr3": lambda: gaia_source.dr2_from_dr3(a["dr3_id"], session),
            "gaia_cone": lambda: gaia_source.cone(a["ra"], a["dec"], a["radius_arcsec"], session),
            "tic_by_id": lambda: tic_source.by_id(a["tic"], timeout),
            "tic_by_gaia_dr2": lambda: tic_source.by_gaia_dr2(a["dr2_id"], timeout),
            "tic_cone": lambda: tic_source.cone(a["ra"], a["dec"], a["radius_arcsec"], timeout),
            "toi_host": lambda: exoplanet_archive.toi_host(a["toi"], session, timeout),
            "koi_host": lambda: exoplanet_archive.koi_host(a["koi"], session, timeout),
        }


class RecordingLookup(_RoutedLookup):
    """Passes each call to `inner` and records its answer under the call's key."""

    def __init__(self, inner: CatalogLookup) -> None:
        self.inner = inner
        self.calls: dict[str, Any] = {}
        self.errors: list[str] = []  # calls that raised; they are not recorded

    def _call(self, method: str, args: dict[str, Any]) -> Any:
        key = _call_key(method, args)
        try:
            answer = getattr(self.inner, method)(**args)
        except Exception as exc:
            self.errors.append(f"{key}: {_describe(exc)}")
            raise
        self.calls[key] = answer
        return answer

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.calls, indent=1, sort_keys=True, allow_nan=False)
        path.write_text(text + "\n", encoding="utf-8")


class ReplayLookup(_RoutedLookup):
    """Answers from a RecordingLookup dump; a call that was not recorded finds nothing."""

    def __init__(self, path: Path) -> None:
        self.calls: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))

    def _call(self, method: str, args: dict[str, Any]) -> Any:
        return self.calls.get(_call_key(method, args), _nothing(method))


# ---- resolution ----


class ResolutionError(Exception):
    """No catalog resolved the target. `tried` lists the failed steps."""

    def __init__(self, target: str, tried: list[str]) -> None:
        self.target = target
        self.tried = list(tried)
        super().__init__(
            f"Could not resolve '{target}'. Tried: {'; '.join(self.tried)}. If this is a catalog "
            "ID, add its prefix (for example 'TIC 420814525', 'Gaia DR3 1779546757669063552', "
            "'HD 209458')."
        )


# How each owner lookup is named in ResolutionError.tried.
_OWNER_STEP = {
    "simbad": "SIMBAD name lookup",
    "gaia_by_id": "Gaia DR3",
    "gaia_dr3_from_dr2": "Gaia DR3 dr2_neighbourhood",
    "gaia_cone": "Gaia DR3 cone",
    "tic_by_id": "TIC v8.2 (MAST)",
    "toi_host": "NASA Exoplanet Archive TOI table",
    "koi_host": "NASA Exoplanet Archive KOI table",
}
# Input kinds whose own catalog is asked first; every other kind is a SIMBAD name.
_OWNED_KINDS = ("tic", "gaia_dr3", "gaia_dr2", "toi", "koi", "coords")
_GAIA_EPOCH = 2016.0
_J2000 = 2000.0
_SIMBAD_XID = {c: re.compile(rf"{c}\s+(\d+)") for c in ("Gaia DR3", "Gaia DR2", "TIC")}
# SIMBAD identifiers kept in StarIdentity.other_ids, keyed by their prefix.
_OTHER_ID_RE = re.compile(
    r"(HD|HIP|HR|GJ|2MASS|KIC|EPIC|TYC|TOI|KOI|Kepler|K2|WASP|HAT-P|KELT)[\s-]")
# Filled from the TIC row when there is no SIMBAD record: (key, TIC column, format).
_TIC_OTHER_IDS = (("HIP", "HIP", "HIP {}"), ("2MASS", "TWOMASS", "2MASS J{}"),
                  ("KIC", "KIC", "KIC {}"))


def _gaia_position(row: dict) -> Position:
    return Position(ra=row["ra"], dec=row["dec"], epoch=row.get("ref_epoch") or _GAIA_EPOCH,
                    pmra=row.get("pmra"), pmdec=row.get("pmdec"), parallax=row.get("parallax"),
                    rv=row.get("radial_velocity"), source="gaia_dr3")


def _simbad_position(record: dict) -> Position | None:
    if record.get("ra") is None or record.get("dec") is None:
        return None
    return Position(ra=record["ra"], dec=record["dec"], epoch=_J2000, pmra=record.get("pmra"),
                    pmdec=record.get("pmdec"), parallax=record.get("plx"), rv=record.get("rv"),
                    source="simbad")


def _tic_position(row: dict) -> Position:
    return Position(ra=row["ra"], dec=row["dec"], epoch=_J2000, pmra=row.get("pmRA"),
                    pmdec=row.get("pmDEC"), source="tic")


def _cone_args(ra: float, dec: float, radius_arcsec: float) -> dict[str, float]:
    """Cone-search arguments.

    The centre is rounded to 1e-7 deg (0.36 mas), so recorded lookups still replay if
    propagate() differs in the last bits on another platform.
    """
    return {"ra": round(float(ra), 7), "dec": round(float(dec), 7), "radius_arcsec": radius_arcsec}


def _simbad_xid(record: dict | None, catalog: str) -> str | None:
    """The first `catalog` number among a SIMBAD record's identifiers."""
    for ident in (record or {}).get("ids") or []:
        m = _SIMBAD_XID[catalog].fullmatch(ident.strip())
        if m:
            return m.group(1)
    return None


def _closest(rows: list[dict]) -> dict | None:
    """The dr2_neighbourhood row with the smallest angular_distance."""
    def distance(row: dict) -> float:
        d = row.get("angular_distance")
        return math.inf if d is None else d

    return min(rows, key=distance, default=None)


def _id_text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


class _Resolution:
    """The state of one resolve() call."""

    def __init__(self, parsed: ParsedTarget, lookup: CatalogLookup, cfg: ResolveConfig) -> None:
        self.parsed = parsed
        self.lookup = lookup
        self.cfg = cfg
        self.warnings: list[str] = []
        self.down: set[str] = set()  # services that failed once; they are not asked again
        self.simbad: dict | None = None
        self.gaia: dict | None = None
        self.tic: dict | None = None
        self.dr2: str | None = None

    def run(self) -> StarIdentity:
        p = self.parsed
        if p.planet:  # W7
            self.warnings.append(f"'{p.raw}' names a planet; this report covers its host star.")
        self._query_owner()
        self._find_simbad()
        self._find_gaia()
        self._find_dr2()
        self._find_tic()
        position = self._position()
        if position is None:  # only a SIMBAD record without coordinates gets here
            raise self._failed(f"{_OWNER_STEP['simbad']}: no position")
        self._check_tic_gaia()
        self._check_close_pair()
        self._check_tic_flag()
        self._check_multiple_system()
        return StarIdentity(
            input=p.raw,
            main_name=self._main_name(),
            simbad_id=self.simbad["main_id"] if self.simbad else None,
            tic_id=self._tic_id,
            gaia_dr3_id=self._dr3_id,
            gaia_dr2_id=self.dr2,
            position=position,
            other_ids=self._other_ids(),
            resolved_via=p.kind if p.kind in _OWNED_KINDS else "simbad",
            warnings=self.warnings,
        )

    @property
    def _tic_id(self) -> int | None:
        return None if self.tic is None else int(self.tic["ID"])

    @property
    def _dr3_id(self) -> str | None:
        return None if self.gaia is None else str(self.gaia["source_id"])

    # -- asking the lookup --

    def _failed(self, step: str) -> ResolutionError:
        return ResolutionError(self.parsed.raw, [step])

    def _ask_owner(self, method: str, **args: Any) -> Any:
        """Ask the catalog that owns the ID; if it fails, nothing resolves."""
        try:
            return getattr(self.lookup, method)(**args)
        except Exception as exc:  # noqa: BLE001 - any failure means the owner is unavailable
            raise self._failed(f"{_OWNER_STEP[method]}: unavailable") from exc

    def _require(self, method: str, **args: Any) -> Any:
        """Ask the owner; no answer also means nothing resolves."""
        answer = self._ask_owner(method, **args)
        if not answer:
            raise self._failed(f"{_OWNER_STEP[method]}: no match")
        return answer

    def _ask(self, method: str, **args: Any) -> Any:
        """Ask a cross-match service.

        A failure becomes a warning (W6), and that service is not asked again during this
        resolution, so one outage costs one timeout.
        """
        service = _SERVICE[_GROUP[method]]
        if service in self.down:
            return _nothing(method)
        try:
            return getattr(self.lookup, method)(**args)
        except Exception as exc:  # noqa: BLE001 - cross-matching is best effort
            self.down.add(service)
            self.warnings.append(
                f"{service} unavailable ({_describe(exc)}); identifiers may be incomplete.")
            return _nothing(method)

    # -- 1. the catalog that owns the ID --

    def _query_owner(self) -> None:
        p = self.parsed
        if p.kind in ("tic", "toi"):
            tic_id = int(p.value) if p.kind == "tic" else self._require("toi_host", toi=p.value)
            self.tic = self._require("tic_by_id", tic=int(tic_id))
        elif p.kind == "gaia_dr3":
            self.gaia = self._require("gaia_by_id", dr3_id=p.value)
        elif p.kind == "gaia_dr2":
            best = _closest(self._require("gaia_dr3_from_dr2", dr2_id=p.value))
            self.gaia = self._require("gaia_by_id", dr3_id=best["dr3_source_id"])
            self.dr2 = p.value
        elif p.kind == "koi":
            kic = self._require("koi_host", koi=p.value)
            self._owner_simbad(f"KIC {kic}")
        elif p.kind == "coords":
            self.gaia = self._match_coordinates(*p.coords)
        else:
            self._owner_simbad(p.query_name)

    def _owner_simbad(self, name: str) -> None:
        self.simbad = self._pick_simbad(name, self._require("simbad", name=name))

    def _match_coordinates(self, ra: float, dec: float) -> dict:
        """The Gaia DR3 source nearest the input at 2016.0 or propagated back to 2000.0.

        The input epoch is unknown, and users usually paste J2000 coordinates, which for a
        high-proper-motion star lie far from its 2016.0 position.
        """
        rows = self._ask_owner("gaia_cone", **_cone_args(ra, dec, self.cfg.coord_search_arcsec))
        best, best_sep = None, math.inf
        for row in rows:
            at_2000 = propagate(_gaia_position(row), _J2000)
            sep = min(separation_arcsec(ra, dec, row["ra"], row["dec"]),
                      separation_arcsec(ra, dec, *at_2000))
            if sep < best_sep:
                best, best_sep = row, sep
        if best is None or best_sep > self.cfg.match_radius_arcsec:
            raise self._failed(
                f"{_OWNER_STEP['gaia_cone']}: no source within {self.cfg.match_radius_arcsec:g}″")
        return best

    def _pick_simbad(self, name: str, records: list[dict]) -> dict:
        """The first object `name` matched; W5 when it matched several."""
        objects: dict[str, dict] = {}
        for record in records:  # one object can come back as several rows
            objects.setdefault(record["main_id"], record)
        chosen = next(iter(objects.values()))
        if len(objects) > 1:
            self.warnings.append(
                f"'{name}' matched {len(objects)} SIMBAD objects; using {chosen['main_id']}.")
        return chosen

    # -- 2-5. cross-matching --

    def _find_simbad(self) -> None:
        if self.simbad is not None:
            return
        names = []
        if self.gaia is not None:
            names.append(f"Gaia DR3 {self._dr3_id}")
        if self.tic is not None:
            names.append(f"TIC {self._tic_id}")
        for name in names:
            records = self._ask("simbad", name=name)
            if records:
                self.simbad = self._pick_simbad(name, records)
                return

    def _find_gaia(self) -> None:
        if self.gaia is not None:
            return
        dr3_id = _simbad_xid(self.simbad, "Gaia DR3")
        if dr3_id:
            self.gaia = self._ask("gaia_by_id", dr3_id=dr3_id)
        tic_dr2 = self.tic.get("GAIA") if self.tic else None
        if self.gaia is None and tic_dr2:
            best = _closest(self._ask("gaia_dr3_from_dr2", dr2_id=tic_dr2))
            if best is not None:
                self.gaia = self._ask("gaia_by_id", dr3_id=best["dr3_source_id"])
                if self.gaia is not None:
                    self.dr2 = tic_dr2
        if self.gaia is None:
            rows = self._cone_here("gaia_cone", _GAIA_EPOCH)
            self.gaia = rows[0] if rows else None

    def _find_dr2(self) -> None:
        if self.dr2 is None:
            self.dr2 = _simbad_xid(self.simbad, "Gaia DR2")
        if self.dr2 is None and self.gaia is not None:
            best = _closest(self._ask("gaia_dr2_from_dr3", dr3_id=self._dr3_id))
            self.dr2 = best["dr2_source_id"] if best else None

    def _find_tic(self) -> None:
        if self.tic is not None:
            return
        tic_id = _simbad_xid(self.simbad, "TIC")
        if tic_id:
            self.tic = self._ask("tic_by_id", tic=int(tic_id))
        if self.tic is None and self.dr2:
            # several TIC entries can share a DR2 ID; prefer one not flagged duplicate/artifact
            rows = self._ask("tic_by_gaia_dr2", dr2_id=self.dr2)
            self.tic = min(rows, key=lambda r: r.get("disposition") is not None, default=None)
        if self.tic is None:
            rows = self._cone_here("tic_cone", _J2000)
            self.tic = rows[0] if rows else None

    def _cone_here(self, method: str, epoch: float) -> list[dict]:
        """A match_radius cone at the best position so far, propagated to `epoch`."""
        position = self._position()
        if position is None:
            return []
        ra, dec = propagate(position, epoch)
        return self._ask(method, **_cone_args(ra, dec, self.cfg.match_radius_arcsec))

    # -- 6-8. the identity --

    def _position(self) -> Position | None:
        """Gaia DR3 if matched; else SIMBAD, then TIC, both at epoch 2000.0."""
        if self.gaia is not None:
            return _gaia_position(self.gaia)
        position = _simbad_position(self.simbad) if self.simbad else None
        if position is None and self.tic is not None:
            position = _tic_position(self.tic)
        return position

    def _main_name(self) -> str:
        if self.simbad is not None:
            return self.simbad["main_id"]
        if self.tic is not None:
            return f"TIC {self._tic_id}"
        return f"Gaia DR3 {self._dr3_id}"

    def _other_ids(self) -> dict[str, str]:
        ids: dict[str, str] = {}
        if self.simbad is not None:
            for ident in self.simbad.get("ids") or []:
                m = _OTHER_ID_RE.match(ident)
                if m:
                    ids.setdefault(m.group(1), " ".join(ident.split()))
        elif self.tic is not None:
            for key, column, template in _TIC_OTHER_IDS:
                if self.tic.get(column):
                    ids[key] = template.format(self.tic[column])
        return ids

    # -- 9. consistency checks --

    def _check_tic_gaia(self) -> None:
        """W1: the TIC's Gaia DR2 ID matches another DR3 source than the one SIMBAD gives."""
        simbad_dr3 = _simbad_xid(self.simbad, "Gaia DR3")
        tic_dr2 = self.tic.get("GAIA") if self.tic else None
        if not (simbad_dr3 and tic_dr2):
            return
        best = _closest(self._ask("gaia_dr3_from_dr2", dr2_id=tic_dr2))
        if best is not None and best["dr3_source_id"] != simbad_dr3:
            self.warnings.append(
                f"TIC {self._tic_id} points to Gaia DR2 {tic_dr2}, which matches Gaia DR3 "
                f"{best['dr3_source_id']}, but SIMBAD gives Gaia DR3 {simbad_dr3}.")

    def _check_close_pair(self) -> None:
        """W2: more Gaia DR3 sources of similar brightness close to the target."""
        target_g = self.gaia.get("phot_g_mean_mag") if self.gaia else None
        if target_g is None:
            return
        cone = _cone_args(self.gaia["ra"], self.gaia["dec"], self.cfg.pair_radius_arcsec)
        count = 1 + sum(  # the target, plus every other source with |ΔG| below the limit
            1 for row in self._ask("gaia_cone", **cone)
            if str(row["source_id"]) != self._dr3_id and row.get("phot_g_mean_mag") is not None
            and abs(row["phot_g_mean_mag"] - target_g) < self.cfg.pair_max_delta_g)
        if count > 1:
            self.warnings.append(
                f"{count} Gaia DR3 sources lie within {self.cfg.pair_radius_arcsec:g}″ with "
                f"ΔG < {self.cfg.pair_max_delta_g:g}; the match may be ambiguous.")

    def _check_tic_flag(self) -> None:
        """W3: the TIC entry is flagged as a split, duplicate or artifact."""
        flag = self.tic.get("disposition") if self.tic else None
        if not flag:
            return
        duplicate = self.tic.get("duplicate_id")
        detail = f" (duplicate of TIC {_id_text(duplicate)})" if duplicate is not None else ""
        self.warnings.append(f"TIC {self._tic_id} is flagged {flag}{detail}.")

    def _check_multiple_system(self) -> None:
        """W4: SIMBAD's object is the parent entry of a multiple system."""
        if self.simbad is None or self.simbad.get("otype") != "**":
            return
        entry = f"SIMBAD's {self.simbad['main_id']} is a multiple-system entry"
        if self.gaia is not None:
            component = f"Gaia DR3 {self._dr3_id}"
        elif self.tic is not None:
            component = f"TIC {self._tic_id}"
        else:
            self.warnings.append(f"{entry}; no single component was matched in Gaia DR3 or TIC.")
            return
        self.warnings.append(
            f"{entry}; catalog data refer to {component}, the source nearest its position.")


def resolve(parsed: ParsedTarget, lookup: CatalogLookup, cfg: ResolveConfig) -> StarIdentity:
    """Resolve a parsed target to one identity across SIMBAD, Gaia DR3 and TIC.

    Raises ResolutionError when the catalog that owns the ID fails or finds nothing.
    """
    return _Resolution(parsed, lookup, cfg).run()
