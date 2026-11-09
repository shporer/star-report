"""The ordered list of sources; registry order is report order."""

from __future__ import annotations

from star_report.config import Config
from star_report.sources.base import Source


def build_sources(cfg: Config) -> list[Source]:
    sources: list[Source] = []
    return sources
