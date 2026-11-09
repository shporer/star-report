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


class ExoFop(Source):
    name = "exofop"
    group = "exofop"
    service = "ExoFOP-TESS"
    needs = ("tic_id",)

    def query(self, ctx: SourceContext) -> dict:
        return {"tic": int(ctx.identity.tic_id)}

    def fetch(self, ctx: SourceContext) -> Any:
        tic = self.query(ctx)["tic"]
        response = ctx.session.get(f"{BASE_URL}?id={tic}&json",
                                   timeout=ctx.config.sources.timeout_s)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "json" not in content_type.lower():
            raise ValueError(f"ExoFOP returned non-JSON content ({content_type})")
        return response.json()

    def parse(self, raw: Any, ctx: SourceContext) -> dict:
        tois = [{"toi": _toi_number(t.get("toi")), "disp_tess": t.get("disp_tess"),
                 "disp_tfop": t.get("disp_tfop"), "sectors": _sectors(t.get("sector")),
                 "notes": t.get("notes")} for t in raw.get("tois") or []]
        ctois = [{"ctoi": c.get("ctoi"), "disposition": c.get("disposition"),
                  "notes": c.get("notes")} for c in raw.get("ctois") or []]
        followup = {key: len(raw.get(key) or []) for key in _FOLLOWUP}
        files: dict[str, int] = {}
        for f in raw.get("files") or []:
            ftype = f.get("ftype")
            if ftype is not None:
                files[ftype] = files.get(ftype, 0) + 1
        if not (tois or ctois or any(followup.values()) or files):
            return {}
        tic = self.query(ctx)["tic"]
        return {"url": f"{BASE_URL}?id={tic}", "tois": tois, "ctois": ctois,
                "followup": followup, "files": files}
