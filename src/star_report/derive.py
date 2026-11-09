"""Names, slugs, the parameter comparison table, disagreement and flags."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

from star_report.config import FlagsConfig, ParametersConfig
from star_report.models import ParamCell, ParamRow, SourceResult, StarIdentity

_PREFIXES = ("NAME ", "V* ", "EM* ", "** ", "* ")


def display_name(main_id: str) -> str:
    name = main_id
    for prefix in _PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return " ".join(name.split())


def slugify(name: str) -> str:
    slug = re.sub(r"\s+", "_", name)
    slug = re.sub(r"[^A-Za-z0-9+._-]", "", slug)
    slug = re.sub(r"_+", "_", slug).strip("._-")
    return slug or "star"


# (key, label, unit, decimals, gaia spec, tic (value, err), simbad key)
# gaia spec: list of (kind, column, note) tried in order; kind "ap" uses
# <column>_lower/_upper, "dist" uses r_lo_/r_hi_, "plain" has no error.
_ROWS: list[tuple[str, str, str, int, list[tuple[str, str, str | None]],
                  tuple[str, str | None] | None, str | None]] = [
    ("teff", "Teff", "K", 0,
     [("ap", "teff_gspspec", "GSP-Spec"), ("ap", "teff_gspphot", "GSP-Phot")],
     ("Teff", "e_Teff"), None),
    ("logg", "log g", "dex", 2,
     [("ap", "logg_gspspec", "GSP-Spec"), ("ap", "logg_gspphot", "GSP-Phot")],
     ("logg", "e_logg"), None),
    ("mh", "[M/H]", "dex", 2,
     [("ap", "mh_gspspec", "GSP-Spec"), ("ap", "mh_gspphot", "GSP-Phot")],
     ("MH", "e_MH"), None),
    ("radius", "Radius", "R☉", 3, [("ap", "radius_flame", "FLAME")], ("rad", "e_rad"), None),
    ("mass", "Mass", "M☉", 3, [("ap", "mass_flame", None)], ("mass", "e_mass"), None),
    ("lum", "Luminosity", "L☉", 3, [("ap", "lum_flame", None)], ("lum", "e_lum"), None),
    ("distance", "Distance", "pc", 1,
     [("dist", "photogeo", "Bailer-Jones photogeo"), ("dist", "geo", None)],
     ("d", "e_d"), None),
    ("age", "Age", "Gyr", 2, [("ap", "age_flame", None)], None, None),
    ("V", "V", "mag", 3, [], ("Vmag", "e_Vmag"), "V"),
    ("G", "G", "mag", 3, [("plain", "phot_g_mean_mag", None)], ("GAIAmag", None), None),
    ("Tmag", "T", "mag", 3, [], ("Tmag", "e_Tmag"), None),
]


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return None if math.isnan(value) else float(value)


def _span_err(lo: Any, hi: Any) -> float | None:
    lo, hi = _num(lo), _num(hi)
    return None if lo is None or hi is None else (hi - lo) / 2


def _gaia_cell(data: Mapping[str, Any], spec: list[tuple[str, str, str | None]]) -> ParamCell | None:
    for kind, col, note in spec:
        if kind == "ap":
            src = data.get("ap") or {}
            value = _num(src.get(col))
            err = _span_err(src.get(f"{col}_lower"), src.get(f"{col}_upper"))
        elif kind == "dist":
            src = data.get("distance") or {}
            value = _num(src.get(f"r_med_{col}"))
            err = _span_err(src.get(f"r_lo_{col}"), src.get(f"r_hi_{col}"))
            note = note or f"Bailer-Jones {col}"
        else:
            value, err = _num(data.get(col)), None
        if value is not None:
            return ParamCell(value=value, err=err, note=note)
    return None


def _cell(data: Mapping[str, Any], value_key: str, err_key: str | None) -> ParamCell | None:
    value = _num(data.get(value_key))
    if value is None:
        return None
    return ParamCell(value=value, err=_num(data.get(err_key)) if err_key else None)


def _ok_data(results: Mapping[str, SourceResult], name: str) -> Mapping[str, Any] | None:
    res = results.get(name)
    if res is None or res.status != "ok" or not isinstance(res.data, Mapping):
        return None
    return res.data


def disagree(a: ParamCell, b: ParamCell, sigma: float, fraction: float,
             absolute: float | None) -> bool:
    if a.value is None or b.value is None:
        return False
    diff = abs(a.value - b.value)
    if a.err is not None and b.err is not None:
        return diff > sigma * math.hypot(a.err, b.err)
    if absolute is not None:
        return diff > absolute
    mean = (abs(a.value) + abs(b.value)) / 2
    return mean > 0 and diff / mean > fraction


def parameter_rows(results: Mapping[str, SourceResult], cfg: ParametersConfig) -> list[ParamRow]:
    gaia = _ok_data(results, "gaia_core")
    tic = _ok_data(results, "tic")
    simbad = _ok_data(results, "simbad")
    rows: list[ParamRow] = []
    for key, label, unit, decimals, gspec, tspec, skey in _ROWS:
        cells: dict[str, ParamCell] = {}
        if gaia is not None and gspec and (c := _gaia_cell(gaia, gspec)):
            cells["gaia"] = c
        if tic is not None and tspec and (c := _cell(tic, *tspec)):
            cells["tic"] = c
        if simbad is not None and skey and (c := _cell(simbad, skey, None)):
            cells["simbad"] = c
        if not cells:
            continue
        values = list(cells.values())
        absolute = cfg.disagreement_absolute.get(key)
        flagged = any(
            disagree(values[i], values[j], cfg.disagreement_sigma,
                     cfg.disagreement_fraction, absolute)
            for i in range(len(values)) for j in range(i + 1, len(values))
        )
        rows.append(ParamRow(key=key, label=label, unit=unit, decimals=decimals,
                             cells=cells, disagree=flagged))
    return rows


def flags(identity: StarIdentity, results: Mapping[str, SourceResult],
          cfg: FlagsConfig) -> list[str]:
    out: list[str] = []
    gaia = _ok_data(results, "gaia_core") or {}
    tic = _ok_data(results, "tic") or {}
    ruwe = _num(gaia.get("ruwe"))
    if ruwe is not None and ruwe > cfg.ruwe_max:
        out.append(f"RUWE {ruwe:.1f}: possible unresolved companion")
    contratio = _num(tic.get("contratio"))
    if contratio is not None and contratio > cfg.contamination_max:
        out.append(f"TIC contamination ratio {contratio:.2f}")
    if gaia.get("phot_variable_flag") == "VARIABLE":
        out.append("Gaia DR3 flags the star as photometrically variable")
    nss = _num(gaia.get("non_single_star"))
    if nss is not None and nss > 0:
        out.append("Gaia DR3 non-single-star flag set")
    return out


def description_line(results: Mapping[str, SourceResult], rows: list[ParamRow]) -> str:
    parts: list[str] = []
    simbad = _ok_data(results, "simbad") or {}
    if sp_type := simbad.get("sp_type"):
        parts.append(str(sp_type))
    by_key = {r.key: r for r in rows}
    vrow = by_key.get("V")
    if vrow:
        cell = vrow.cells.get("simbad") or vrow.cells.get("tic")
        if cell and cell.value is not None:
            parts.append(f"V = {cell.value:.2f}")
    drow = by_key.get("distance")
    if drow:
        cell = drow.cells.get("gaia") or drow.cells.get("tic")
        if cell and cell.value is not None:
            parts.append(f"{cell.value:.1f} pc")
    return ", ".join(parts)
