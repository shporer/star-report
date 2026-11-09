"""VizieR sources: a shared framework plus the companion and variability catalogs."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import astropy.units as u
from astropy.coordinates import SkyCoord
from astroquery.vizier import Vizier

from star_report.config import Config
from star_report.coords import propagate, separation_arcsec
from star_report.sources.base import Source, SourceContext, pick_server, records

_POSITION_COLUMNS = ["_RAJ2000", "_DEJ2000"]
# Constraint queries ask for the catalog's default columns: on the Harvard mirror an explicit column
# list next to a constraint sometimes makes VizieR return an empty table (B/sb9/orbits with
# omega/T0/V0, the El-Badry catalog with some subsets). The defaults carry every field the parsers use.
CONSTRAINT_COLUMNS: tuple[str, ...] = ()

# A follow-up runs after the cone search: (ctx, catalog, tables so far) -> extra tables to merge in.
FollowUp = Callable[[SourceContext, str, dict], dict]


@dataclass(frozen=True)
class VizierSpec:
    name: str
    catalog_key: str
    columns: tuple[str, ...]
    mode: Literal["cone", "gaia_id"] = "cone"
    # gaia_id mode: one constraint query per column. If the star has no usable (digits-only) Gaia
    # DR3 ID, the source falls back to a cone search, but only when cfg.sources.radius_arcsec has an
    # entry under this spec's `name`; with no radius configured the query is skipped (empty payload).
    gaia_columns: tuple[str, ...] = ()
    table: str = ""                      # cone mode: table within the catalog ("" = the catalog itself)
    followup: FollowUp | None = None     # optional second step run on the cone result
    needs: tuple[str, ...] = ()


def vizier_client(cfg: Config, columns: Sequence[str], row_limit: int = 200,
                  timeout: float | None = None) -> Vizier:
    """A client on the first reachable VizieR host. The catalog goes to each query call."""
    return Vizier(vizier_server=pick_server(cfg.servers.vizier), columns=list(columns),
                  row_limit=row_limit, timeout=cfg.sources.timeout_s if timeout is None else timeout)


def _named(result: Any) -> list[tuple[str, Any]]:
    """(name, table) pairs from an astroquery TableList, or from a plain list of tables."""
    if result is None:
        return []
    if isinstance(result, Mapping):
        return [(str(k), t) for k, t in result.items()]
    return [(str(getattr(t, "meta", {}).get("name", i)), t) for i, t in enumerate(result)]


def _to_records(result: Any) -> dict[str, list[dict]]:
    return {name: records(table) for name, table in _named(result)}


def digits_only(value: Any) -> str | None:
    """The value as a digit string when it is an int or an all-digit string, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value >= 0 else None
    text = value.strip() if isinstance(value, str) else ""
    return text if text.isascii() and text.isdigit() else None


class VizierSource(Source):
    group = "vizier"

    def __init__(self, spec: VizierSpec, parser: Callable[[Any, SourceContext], Any]) -> None:
        self.spec = spec
        self._parser = parser
        self.name = spec.name
        self.needs = spec.needs

    def catalog(self, ctx: SourceContext) -> str:
        return ctx.config.sources.vizier_catalogs[self.spec.catalog_key]

    @property
    def service(self) -> str:  # type: ignore[override]
        return f"VizieR {self.spec.catalog_key}"

    def _use_cone(self, ctx: SourceContext) -> bool:
        if self.spec.mode == "cone":
            return True
        # gaia_id mode falls back to a cone only when the star has no usable Gaia ID
        # and a cone radius is configured for this source.
        return (digits_only(ctx.identity.gaia_dr3_id) is None
                and self.spec.name in ctx.config.sources.radius_arcsec)

    def query(self, ctx: SourceContext) -> dict:
        q: dict[str, Any] = {"catalog": self.catalog(ctx)}
        if self._use_cone(ctx):
            ra, dec = propagate(ctx.identity.position, 2000.0)
            q.update(ra=ra, dec=dec, radius_arcsec=self._radius(ctx))
        else:
            q["gaia_dr3_id"] = ctx.identity.gaia_dr3_id
        return q

    def _radius(self, ctx: SourceContext) -> float:
        return float(ctx.config.sources.radius_arcsec[self.spec.name])

    def fetch(self, ctx: SourceContext) -> Any:
        catalog = self.catalog(ctx)
        if self._use_cone(ctx):
            return self._fetch_cone(ctx, catalog)
        gaia_id = digits_only(ctx.identity.gaia_dr3_id)
        if gaia_id is None:
            return {}
        client = vizier_client(ctx.config, CONSTRAINT_COLUMNS, timeout=self.timeout_s(ctx.config))
        out: dict[str, list[dict]] = {}
        for column in self.spec.gaia_columns:
            # Constraint values are bare: a leading "=" matches nothing.
            result = client.query_constraints(catalog=catalog, **{column: gaia_id})
            out[column] = [row for rows in _to_records(result).values() for row in rows]
        return out

    def _fetch_cone(self, ctx: SourceContext, catalog: str) -> dict[str, list[dict]]:
        ra, dec = propagate(ctx.identity.position, 2000.0)
        client = vizier_client(ctx.config, [*self.spec.columns, *_POSITION_COLUMNS],
                               timeout=self.timeout_s(ctx.config))
        target = f"{catalog}/{self.spec.table}" if self.spec.table else catalog
        result = client.query_region(
            SkyCoord(float(ra) * u.deg, float(dec) * u.deg, frame="icrs"),
            radius=self._radius(ctx) * u.arcsec, catalog=target)
        tables = _to_records(result)
        if self.spec.followup and any(tables.values()):
            tables.update(self.spec.followup(ctx, catalog, tables))
        return tables

    def parse(self, raw: Any, ctx: SourceContext) -> Any:
        return self._parser(raw, ctx)


# ---- tolerant parsing helpers ----

def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value == value else None


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _rows(raw: Any, suffix: str | None = None) -> list[dict]:
    """Dict rows from a {table_name: [row, ...]} payload; `suffix` selects tables by name ending."""
    if not isinstance(raw, dict):
        return []
    out: list[dict] = []
    for name, rows in raw.items():
        if suffix is not None and not str(name).endswith(suffix):
            continue
        if isinstance(rows, list):
            out.extend(r for r in rows if isinstance(r, dict))
    return out


def _dist(row: dict, ctx: SourceContext) -> float | None:
    ra, dec = _num(row.get("_RAJ2000")), _num(row.get("_DEJ2000"))
    if ra is None or dec is None:
        return None
    t_ra, t_dec = propagate(ctx.identity.position, 2000.0)
    return separation_arcsec(t_ra, t_dec, ra, dec)


def _by_dist(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (r["dist_arcsec"] is None, r["dist_arcsec"] or 0.0))


# ---- parsers ----

def parse_wds(raw: Any, ctx: SourceContext) -> list[dict]:
    out = []
    for r in _rows(raw):
        has2 = _num(r.get("sep2")) is not None   # the latest measurement, at epoch Obs2
        k = "2" if has2 else "1"
        out.append({
            "wds": _text(r.get("WDS")), "disc": _text(r.get("Disc")), "comp": _text(r.get("Comp")),
            "last_obs": _num(r.get(f"Obs{k}")), "pa": _num(r.get(f"pa{k}")),
            "sep_arcsec": _num(r.get(f"sep{k}")),
            "mag1": _num(r.get("mag1")), "mag2": _num(r.get("mag2")),
            "dist_arcsec": _dist(r, ctx),
        })
    return _by_dist(out)


def _seq(value: Any) -> str | None:
    n = _num(value)
    return str(int(n)) if n is not None and n >= 0 and n == int(n) else None


def sb9_orbits(ctx: SourceContext, catalog: str, tables: dict) -> dict:
    """Follow-up for SB9: one orbits query per matched Seq."""
    seqs = sorted({s for r in _rows(tables, "main") if (s := _seq(r.get("Seq"))) is not None})
    if not seqs:
        return {}
    client = vizier_client(ctx.config, CONSTRAINT_COLUMNS)
    rows: list[dict] = []
    for seq in seqs:
        result = client.query_constraints(catalog=f"{catalog}/orbits", Seq=seq)
        rows.extend(r for rs in _to_records(result).values() for r in rs)
    return {f"{catalog}/orbits": rows}


def parse_sb9(raw: Any, ctx: SourceContext) -> list[dict]:
    orbits: dict[str, list[dict]] = {}
    for r in _rows(raw, "orbits"):
        if (s := _seq(r.get("Seq"))) is not None:
            orbits.setdefault(s, []).append(r)
    out = []
    for m in _rows(raw, "main"):
        # a match without an orbit row still reports the system
        for o in orbits.get(_seq(m.get("Seq")) or "", [{}]):
            out.append({
                "name": _text(m.get("Name")), "period_d": _num(o.get("Per")),
                "ecc": _num(o.get("e")), "k1_kms": _num(o.get("K1")), "k2_kms": _num(o.get("K2")),
                "grade": _num(o.get("Grade")), "dist_arcsec": _dist(m, ctx),
            })
    return [{k: v for k, v in r.items() if k != "dist_arcsec"} for r in _by_dist(out)]


def parse_vsx(raw: Any, ctx: SourceContext) -> list[dict]:
    return _by_dist([{
        "name": _text(r.get("Name")), "type": _text(r.get("Type")),
        "period_d": _num(r.get("Period")), "max": _num(r.get("max")), "min": _num(r.get("min")),
        "dist_arcsec": _dist(r, ctx),
    } for r in _rows(raw)])


def _arcsec_from_deg(value: Any) -> float | None:
    """El-Badry theta: VizieR serves it in degrees (the brief said arcsec)."""
    n = _num(value)
    return None if n is None else n * 3600.0


def parse_wide_binaries(raw: Any, ctx: SourceContext) -> list[dict]:
    target = digits_only(ctx.identity.gaia_dr3_id)
    out = []
    for r in _rows(raw):
        s1, s2 = digits_only(r.get("Source1")), digits_only(r.get("Source2"))
        out.append({
            "companion": s1 if s2 == target else s2,   # the member that is not the target
            "sep_arcsec": _arcsec_from_deg(r.get("theta")), "sep_au": _num(r.get("sepAU")),
            "R_chance": _num(r.get("R")), "type": _text(r.get("BinType")),
        })
    return sorted(out, key=lambda r: (r["sep_arcsec"] is None, r["sep_arcsec"] or 0.0))


WDS = VizierSource(VizierSpec(
    "wds", "wds", ("WDS", "Disc", "Comp", "Obs1", "Obs2", "Nobs", "pa1", "pa2", "sep1", "sep2",
                   "mag1", "mag2", "SpType")), parse_wds)
SB9 = VizierSource(VizierSpec(
    "sb9", "sb9", ("Seq", "Name", "Sp1", "Sp2"),
    table="main", followup=sb9_orbits), parse_sb9)
VSX = VizierSource(VizierSpec(
    "vsx", "vsx", ("Name", "OID", "Type", "Period", "max", "min", "Sp")), parse_vsx)
WIDE_BINARIES = VizierSource(VizierSpec(
    "wide_binaries", "wide_binaries", ("Source1", "Source2", "theta", "sepAU", "R", "BinType"),
    mode="gaia_id", gaia_columns=("Source1", "Source2"), needs=("gaia_dr3_id",)), parse_wide_binaries)


# ---- survey spectra and RVs ----

def harps_rvs(ctx: SourceContext, catalog: str, tables: dict) -> dict:
    """Follow-up for the HARPS RV bank: the BJD of every RV of each matched star (key differs from the cone table)."""
    names = sorted({n for r in _rows(tables) if (n := _text(r.get("Name")))})
    if not names:
        return {}
    client = vizier_client(ctx.config, ["BJD"], row_limit=-1)
    rows: list[dict] = []
    for name in names:
        result = client.query_constraints(catalog=f"{catalog}/rvbank", Name=name)
        rows.extend(r for rs in _to_records(result).values() for r in rs)
    return {f"{catalog}/rvbank": rows}


def parse_harps_rvbank(raw: Any, ctx: SourceContext) -> dict:
    stars = _rows(raw, "list")
    if not stars:
        return {}
    star = min(stars, key=lambda r: (_dist(r, ctx) is None, _dist(r, ctx) or 0.0))
    name = _text(star.get("Name"))
    bjds = [b for r in _rows(raw, "rvbank") if _text(r.get("Name")) in (None, name)
            and (b := _num(r.get("BJD"))) is not None]
    nrv = _num(star.get("Nrv"))
    first, last = (min(bjds), max(bjds)) if bjds else (None, None)
    return {"name": name, "n_rv": len(bjds) or (int(nrv) if nrv is not None else None),
            "first_bjd": first, "last_bjd": last,
            "span_yr": (last - first) / 365.25 if bjds else None}


def _best_snr(rows: list[dict], *keys: str) -> dict | None:
    """The row with the highest S/N (the largest of `keys`); rows without any S/N rank last."""
    def snr(r: dict) -> float:
        return max((v for k in keys if (v := _num(r.get(k))) is not None), default=float("-inf"))
    return max(rows, key=snr) if rows else None


def parse_apogee(raw: Any, ctx: SourceContext) -> dict:
    row = _best_snr(_rows(raw), "SNR")
    if row is None:
        return {}
    nvis = _num(row.get("Nvis"))
    return {"apogee_id": _text(row.get("APOGEE")), "teff": _num(row.get("Teff")),
            "logg": _num(row.get("logg")), "mh": _num(row.get("[M/H]")),
            "n_visits": int(nvis) if nvis is not None else None,
            "snr": _num(row.get("SNR")), "rv": _num(row.get("HRV"))}


_LAMOST_SNR = ("snru", "snrg", "snrr", "snri", "snrz")


def parse_lamost(raw: Any, ctx: SourceContext) -> dict:
    rows = _rows(raw)
    best = _best_snr(rows, *_LAMOST_SNR)
    if best is None:
        return {}
    dates = [d for r in rows if (d := _text(r.get("Obs.Date")))]
    return {"n_spectra": len(rows), "last_obs": max(dates) if dates else None,
            "teff": _num(best.get("Teff")), "logg": _num(best.get("logg")),
            "feh": _num(best.get("[Fe/H]"))}


HARPS_RVBANK = VizierSource(VizierSpec(
    "harps_rvbank", "harps_rvbank", ("Name", "Nrv", "SimbadName"),
    table="list", followup=harps_rvs), parse_harps_rvbank)
APOGEE = VizierSource(VizierSpec(
    "apogee", "apogee", ("APOGEE", "Teff", "logg", "[M/H]", "Nvis", "SNR", "HRV", "GaiaEDR3"),
    mode="gaia_id", gaia_columns=("GaiaEDR3",), needs=("gaia_dr3_id",)), parse_apogee)
LAMOST = VizierSource(VizierSpec(
    "lamost", "lamost", ("ObsID", "Obs.Date", "snru", "snrg", "snrr", "snri", "snrz",
                         "Teff", "logg", "[Fe/H]")), parse_lamost)
