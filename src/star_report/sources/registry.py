"""The ordered list of sources; registry order is report order."""

from __future__ import annotations

from star_report.config import Config
from star_report.sources.base import Source
from star_report.sources.exoplanet_archive import ExoplanetArchive
from star_report.sources.exofop import ExoFop
from star_report.sources.gaia import GaiaCore, GaiaNeighbors, GaiaNss, GaiaVari
from star_report.sources.mast import MastObservations
from star_report.sources.simbad import Simbad
from star_report.sources.tess_pointing import TessSectors
from star_report.sources.tic import Tic
from star_report.sources.vizier import SB9, VSX, WDS, WIDE_BINARIES


def build_sources(cfg: Config) -> list[Source]:
    sources: list[Source] = [Simbad(), GaiaCore(), GaiaNeighbors(), Tic(), ExoplanetArchive(),
                       ExoFop(), GaiaNss(), GaiaVari(), WDS, SB9, VSX, WIDE_BINARIES,
                       MastObservations(), TessSectors()]
    return sources
