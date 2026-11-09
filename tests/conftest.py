import pytest


def pytest_collection_modifyitems(items):
    for item in items:
        if item.get_closest_marker("live"):
            item.add_marker(pytest.mark.enable_socket)


# ---- shared helpers for source tests and the fixture recorder ----
import json  # noqa: E402
import tempfile  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import yaml  # noqa: E402

from star_report.cache import Cache  # noqa: E402
from star_report.config import Config, load_config  # noqa: E402
from star_report.models import Position, StarIdentity  # noqa: E402
from star_report.sources.base import SourceContext, make_session  # noqa: E402

TESTS_DIR = Path(__file__).parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
REFERENCE_STARS = TESTS_DIR / "reference_stars.yaml"
DEFAULT_STAR = "HD_209458"


def all_ref_stars() -> list[dict]:
    return yaml.safe_load(REFERENCE_STARS.read_text())


def ref_star(slug: str) -> dict:
    for star in all_ref_stars():
        if star["slug"] == slug:
            return star
    raise KeyError(slug)


def ref_identity(slug: str) -> StarIdentity:
    star = ref_star(slug)
    return StarIdentity(
        input=star["simbad_main_id"],
        main_name=star["simbad_main_id"],
        simbad_id=star["simbad_main_id"],
        tic_id=star["tic"],
        gaia_dr3_id=star["gaia_dr3"],
        gaia_dr2_id=star["gaia_dr2"],
        position=Position(**star["position"], source="gaia_dr3"),
        other_ids=dict(star["other_ids"]),
        resolved_via="simbad",
    )


def load_fixture(source: str, slug: str) -> Any:
    return json.loads((FIXTURES_DIR / source / f"{slug}.json").read_text())


def make_ctx(
    identity: StarIdentity | None = None,
    cfg: Config | None = None,
    cache_dir: Path | None = None,
    now: datetime | None = None,
) -> SourceContext:
    """A context on a throwaway cache directory (never the real user cache)."""
    cfg = cfg or load_config(cwd=Path(tempfile.gettempdir()))
    cache_dir = cache_dir or Path(tempfile.mkdtemp(prefix="star-report-test-cache-"))
    cache_kwargs: dict[str, Any] = {}
    if now is not None:
        cache_kwargs["now"] = lambda: now
    cache = Cache(cache_dir, cfg.cache.expiry_days, cfg.cache.empty_expiry_days, **cache_kwargs)
    ctx_kwargs: dict[str, Any] = {}
    if now is not None:
        ctx_kwargs["now"] = lambda: now
    return SourceContext(
        identity=identity or ref_identity(DEFAULT_STAR),
        config=cfg,
        cache=cache,
        session=make_session(),
        **ctx_kwargs,
    )


@pytest.fixture
def ctx(tmp_path):
    return make_ctx(cache_dir=tmp_path / "cache")


# ---- full-report builder for render tests ----
from star_report import derive  # noqa: E402
from star_report.models import LLMBlock, Provenance, Report, SourceResult  # noqa: E402
from star_report.sources.registry import build_sources  # noqa: E402

REPORT_SOURCES = ["simbad", "gaia_core", "gaia_neighbors", "tic", "exoplanet_archive",
                  "exofop", "gaia_nss", "gaia_vari", "wds", "sb9", "vsx", "wide_binaries",
                  "mast", "tess_pointing", "eso", "koa"]
_FIXED_TIME = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)


def make_report(slug: str = DEFAULT_STAR, **overrides) -> Report:
    """A deterministic Report built by parsing the recorded fixtures offline."""
    cfg = load_config(cwd=Path(tempfile.gettempdir()))
    sources = {s.name: s for s in build_sources(cfg)}
    identity = ref_identity(slug)
    ctx = make_ctx(identity=identity, cfg=cfg)
    results: dict[str, SourceResult] = {}
    for name in REPORT_SOURCES:
        src = sources[name]
        data = src.parse(load_fixture(name, slug), ctx)
        results[name] = SourceResult(
            source=name,
            status="ok" if data else "empty",
            data=data,
            provenance=Provenance(service=src.service, query=json.dumps(src.query(ctx), default=str),
                                  catalog_version=src.catalog_version, retrieved=_FIXED_TIME,
                                  from_cache=True),
            elapsed_s=0.4,
        )
    rows = derive.parameter_rows(results, cfg.parameters)
    report = Report(
        tool_version="0.1.0",
        generated=_FIXED_TIME,
        identity=identity,
        results=results,
        parameters=rows,
        flags=derive.flags(identity, results, cfg.flags),
        llm=LLMBlock(status="skipped", reason="LLM disabled (--no-llm or config)"),
    )
    return report.model_copy(update=overrides) if overrides else report


# ---- a fake sky shared by resolver and pipeline tests ----
from dataclasses import dataclass, field  # noqa: E402

from star_report.coords import separation_arcsec  # noqa: E402


def _cone(rows, ra, dec, radius_arcsec, dist_key):
    hits = []
    for row in rows:
        dist = separation_arcsec(ra, dec, row["ra"], row["dec"])
        if dist <= radius_arcsec:
            hits.append({**row, dist_key: dist})
    return sorted(hits, key=lambda r: r[dist_key])


def _neighbourhood(dr2_id, dr3_id):
    return {"dr2_source_id": dr2_id, "dr3_source_id": dr3_id,
            "angular_distance": 1.0, "magnitude_difference": 0.0}


@dataclass
class FakeLookup:
    """A dict-backed CatalogLookup over a tiny fake sky; cones search the stored rows.

    `errors` maps a method name to the exception that method raises.
    """

    simbad_objects: list[dict] = field(default_factory=list)  # matched by main_id or any of ids
    gaia_rows: dict[str, dict] = field(default_factory=dict)  # Gaia DR3 ID -> row
    dr2_to_dr3: dict[str, str] = field(default_factory=dict)
    tic_rows: dict[int, dict] = field(default_factory=dict)  # TIC ID -> row
    tois: dict[str, int] = field(default_factory=dict)  # TOI number -> host TIC ID
    kois: dict[str, int] = field(default_factory=dict)  # KOI number -> host KIC ID
    errors: dict[str, Exception] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def _enter(self, method):
        self.calls.append(method)
        if method in self.errors:
            raise self.errors[method]

    def simbad(self, name):
        self._enter("simbad")
        return [o for o in self.simbad_objects if name == o["main_id"] or name in o["ids"]]

    def gaia_by_id(self, dr3_id):
        self._enter("gaia_by_id")
        return self.gaia_rows.get(dr3_id)

    def gaia_dr3_from_dr2(self, dr2_id):
        self._enter("gaia_dr3_from_dr2")
        return [_neighbourhood(d2, d3) for d2, d3 in self.dr2_to_dr3.items() if d2 == dr2_id]

    def gaia_dr2_from_dr3(self, dr3_id):
        self._enter("gaia_dr2_from_dr3")
        return [_neighbourhood(d2, d3) for d2, d3 in self.dr2_to_dr3.items() if d3 == dr3_id]

    def gaia_cone(self, ra, dec, radius_arcsec):
        self._enter("gaia_cone")
        return _cone(self.gaia_rows.values(), ra, dec, radius_arcsec, "dist_arcsec")

    def tic_by_id(self, tic):
        self._enter("tic_by_id")
        return self.tic_rows.get(tic)

    def tic_by_gaia_dr2(self, dr2_id):
        self._enter("tic_by_gaia_dr2")
        return [r for r in self.tic_rows.values() if r.get("GAIA") == dr2_id]

    def tic_cone(self, ra, dec, radius_arcsec):
        self._enter("tic_cone")
        return _cone(self.tic_rows.values(), ra, dec, radius_arcsec, "dstArcSec")

    def toi_host(self, toi):
        self._enter("toi_host")
        return self.tois.get(toi)

    def koi_host(self, koi):
        self._enter("koi_host")
        return self.kois.get(koi)


RA, DEC = 150.0, 20.0


def gaia_row(source_id, ra=RA, dec=DEC, g=10.0, **extra):
    return {"source_id": source_id, "ra": ra, "dec": dec, "ref_epoch": 2016.0, "parallax": 10.0,
            "pmra": 0.0, "pmdec": 0.0, "radial_velocity": None, "phot_g_mean_mag": g,
            "bp_rp": 0.8, **extra}


def tic_row(tic_id, ra=RA, dec=DEC, **extra):
    return {"ID": tic_id, "ra": ra, "dec": dec, "pmRA": 0.0, "pmDEC": 0.0, "GAIA": None,
            "disposition": None, "duplicate_id": None, "HIP": None, "TWOMASS": None, "KIC": None,
            "Tmag": 9.5, **extra}


def simbad_obj(main_id, ids=(), ra=RA, dec=DEC, otype="*", **extra):
    return {"main_id": main_id, "ra": ra, "dec": dec, "otype": otype, "sp_type": "G2V",
            "rv": None, "nbref": 10, "pmra": 0.0, "pmdec": 0.0, "plx": 10.0, "B": None,
            "V": 9.0, "ids": [main_id, *ids], **extra}
