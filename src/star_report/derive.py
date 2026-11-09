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
    out.extend(_companion_flags(results))
    return out


def _ok_rows(results: Mapping[str, SourceResult], name: str) -> list[Mapping[str, Any]]:
    res = results.get(name)
    if res is None or res.status != "ok" or not isinstance(res.data, list):
        return []
    return [r for r in res.data if isinstance(r, Mapping)]


def _dict_rows(value: Any) -> list[Mapping[str, Any]]:
    return [r for r in value if isinstance(r, Mapping)] if isinstance(value, list) else []


def _tagged(items: list[tuple[Any, Any]]) -> list[str]:
    return [f"{ident} ({disp})" if disp else str(ident) for ident, disp in items if ident]


def _companion_flags(results: Mapping[str, SourceResult]) -> list[str]:
    out: list[str] = []
    archive = _ok_data(results, "exoplanet_archive")
    exofop = _ok_data(results, "exofop")

    planets = _dict_rows(archive.get("planets")) if archive else []
    if planets:
        n = len(planets)
        out.append(f"{n} confirmed planet{'s' if n != 1 else ''}")
    if archive is not None:
        tois = _tagged([(t.get("toi"), t.get("disposition")) for t in _dict_rows(archive.get("tois"))])
    elif exofop is not None:
        tois = _tagged([(t.get("toi"), t.get("disp_tfop")) for t in _dict_rows(exofop.get("tois"))])
    else:
        tois = []
    if tois:
        out.append("TOIs: " + ", ".join(tois))
    kois = _tagged([(k.get("koi"), k.get("disposition"))
                    for k in _dict_rows(archive.get("kois"))]) if archive else []
    if kois:
        out.append("KOIs: " + ", ".join(kois))

    for row in _ok_rows(results, "gaia_nss"):
        kind, period = row.get("type"), _num(row.get("period_d"))
        if not kind:
            continue
        if period is not None:
            out.append(f"Gaia DR3 {kind} orbit, P = {period:.2f} d")
        else:
            out.append(f"Gaia DR3 {kind} solution")

    vari = _ok_data(results, "gaia_vari") or {}
    for cls in _dict_rows(vari.get("classes")):
        if cls.get("class"):
            out.append(f"Gaia DR3 variability class: {cls['class']}")
    for per in _dict_rows(vari.get("periods")):
        period = _num(per.get("period_d"))
        if per.get("type") and period is not None:
            out.append(f"Gaia DR3 {per['type']} period {period:.3f} d")

    for row in _ok_rows(results, "vsx"):
        if not row.get("name") or not row.get("type"):
            continue
        text = f"VSX: {row['name']}, type {row['type']}"
        period = _num(row.get("period_d"))
        if period is not None:
            text += f", P = {period:.10g} d"
        out.append(text)

    for row in _ok_rows(results, "wds"):
        sep = _num(row.get("sep_arcsec"))
        if row.get("wds") is None or sep is None:
            continue
        label = " ".join(str(row[k]) for k in ("wds", "disc", "comp") if row.get(k))
        text = f"WDS {label}: {sep:.1f}″"
        if row.get("last_obs") is not None:
            text += f" (observed {row['last_obs']})"
        out.append(text)

    for row in _ok_rows(results, "sb9"):
        period = _num(row.get("period_d"))
        if period is None:
            continue
        text = f"SB9 orbit: P = {period:.10g} d"
        k1 = _num(row.get("k1_kms"))
        if k1 is not None:
            text += f", K1 = {k1:.10g} km/s"
        out.append(text)

    for row in _ok_rows(results, "wide_binaries"):
        sep = _num(row.get("sep_arcsec"))
        if row.get("companion") is None or sep is None:
            continue
        text = f"Gaia wide binary: Gaia DR3 {row['companion']} at {sep:.1f}″"
        sep_au = _num(row.get("sep_au"))
        if sep_au is not None:
            text += f" ({sep_au:.0f} au)"
        chance = _num(row.get("R_chance"))
        if chance is not None:
            text += f", R = {chance:.2g}"
        out.append(text)
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
