"""Pydantic data model for a star report (serialized as data.json, schema_version 1)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

Status = Literal["ok", "empty", "error", "timeout", "skipped"]

# "[bibcode]" citation marker (19-char bibcode)
_BIBCODE = r"[0-9]{4}[A-Za-z0-9&.]{14}[A-Za-z.]"
BIBCODE_RE = re.compile(rf"\[({_BIBCODE})\]")
# A citation bracket: one bibcode, or several separated by commas or semicolons ([A], [A, B], [A; B]).
CITATION_RE = re.compile(rf"\[\s*{_BIBCODE}(?:\s*[,;]\s*{_BIBCODE})*\s*\]")
BIBCODE_PART_RE = re.compile(_BIBCODE)   # picks the bibcodes out of a CITATION_RE match


class Position(BaseModel):
    ra: float
    dec: float
    epoch: float  # 2016.0 for Gaia DR3, 2000.0 for SIMBAD/TIC
    pmra: float | None = None  # mas/yr, includes cos dec
    pmdec: float | None = None  # mas/yr
    parallax: float | None = None  # mas
    rv: float | None = None  # km/s
    source: Literal["gaia_dr3", "simbad", "tic"]


class StarIdentity(BaseModel):
    input: str
    main_name: str  # SIMBAD main_id, or "TIC n" / "Gaia DR3 n"
    simbad_id: str | None = None
    tic_id: int | None = None
    gaia_dr3_id: str | None = None
    gaia_dr2_id: str | None = None
    position: Position
    other_ids: dict[str, str] = {}  # "HD", "HIP", "2MASS", "KIC", "EPIC", "TOI", ...
    resolved_via: str  # "tic" | "gaia_dr3" | "gaia_dr2" | "toi" | "koi" | "coords" | "simbad"
    warnings: list[str] = []


class Provenance(BaseModel):
    service: str
    query: str
    catalog_version: str | None = None
    retrieved: datetime
    from_cache: bool


class SourceResult(BaseModel):
    source: str
    status: Status
    data: Any = None
    provenance: Provenance | None = None
    error: str | None = None
    elapsed_s: float = 0.0


class ParamCell(BaseModel):
    value: float | None = None
    err: float | None = None
    note: str | None = None  # e.g. "GSP-Spec"


class ParamRow(BaseModel):
    key: str
    label: str
    unit: str
    decimals: int
    cells: dict[str, ParamCell]  # keys among "gaia", "tic", "simbad"
    disagree: bool = False


class Paper(BaseModel):
    bibcode: str
    title: str
    year: int | None = None
    pubdate: str | None = None
    authors: list[str] = []
    citation_count: int = 0
    abstract: str | None = None
    doctype: str | None = None


class LLMBlock(BaseModel):
    status: Literal["ok", "skipped", "failed"]
    reason: str | None = None
    overview: str | None = None
    literature_summary: str | None = None
    cited_bibcodes: list[str] = []
    model: str | None = None
    effort: str | None = None
    generated: datetime | None = None
    bibcodes_sent: list[str] = []
    from_cache: bool = False


class Report(BaseModel):
    schema_version: Literal[1] = 1
    tool_version: str
    generated: datetime
    identity: StarIdentity
    results: dict[str, SourceResult]
    parameters: list[ParamRow] = []
    flags: list[str] = []
    plots: dict[str, str] = {}  # name -> "plots/<name>.png"
    plot_notes: dict[str, str] = {}  # name -> note
    llm: LLMBlock
