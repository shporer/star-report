"""ExoFOP-TESS: TOI/CTOI dispositions and follow-up observation counts for a TIC target."""

from __future__ import annotations

import re
from typing import Any

from star_report.sources.base import Source, SourceContext

BASE_URL = "https://exofop.ipac.caltech.edu/tess/target.php"
_FOLLOWUP = ("imaging", "spectroscopy", "time_series")


def _sectors(value: Any) -> list[int]:
    return [int(n) for n in re.findall(r"\d+", "" if value is None else str(value))]


def _toi_number(value: Any) -> str | None:
    """'TOI 5972.01' -> '5972.01'."""
    if value is None:
        return None
    return re.sub(r"^\s*TOI[\s-]*", "", str(value), flags=re.IGNORECASE).strip()


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _dicts(value: Any) -> list[dict]:
    return [v for v in _as_list(value) if isinstance(v, dict)]


def _tic(value: Any) -> int | None:
    """The TIC ID as an int when it is an integer or an all-digit string, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = value.strip() if isinstance(value, str) else ""
    return int(text) if text.isascii() and text.isdigit() else None


class ExoFop(Source):
    name = "exofop"
    group = "exofop"
    service = "ExoFOP-TESS"
    needs = ("tic_id",)

    def skip_reason(self, ctx: SourceContext) -> str | None:
        return None if _tic(ctx.identity.tic_id) is not None else "no TIC ID"

    def query(self, ctx: SourceContext) -> dict:
        return {"tic": _tic(ctx.identity.tic_id)}

    def fetch(self, ctx: SourceContext) -> Any:
        tic = _tic(ctx.identity.tic_id)
        response = ctx.session.get(f"{BASE_URL}?id={int(tic)}&json",
                                   timeout=self.timeout_s(ctx.config))
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "json" not in content_type.lower():
            raise ValueError(f"ExoFOP returned non-JSON content ({content_type})")
        return response.json()

    def parse(self, raw: Any, ctx: SourceContext) -> dict:
        if not isinstance(raw, dict):
            raw = {}
        tois = [{"toi": _toi_number(t.get("toi")), "disp_tess": t.get("disp_tess"),
                 "disp_tfop": t.get("disp_tfop"), "sectors": _sectors(t.get("sector")),
                 "notes": t.get("notes")} for t in _dicts(raw.get("tois"))]
        ctois = [{"ctoi": c.get("ctoi"), "disposition": c.get("disposition"),
                  "notes": c.get("notes")} for c in _dicts(raw.get("ctois"))]
        followup = {key: len(_as_list(raw.get(key))) for key in _FOLLOWUP}
        files: dict[str, int] = {}
        for f in _dicts(raw.get("files")):
            ftype = f.get("ftype")
            if isinstance(ftype, str):
                files[ftype] = files.get(ftype, 0) + 1
        if not (tois or ctois or any(followup.values()) or files):
            return {}
        return {"url": f"{BASE_URL}?id={_tic(ctx.identity.tic_id)}", "tois": tois,
                "ctois": ctois, "followup": followup, "files": files}
