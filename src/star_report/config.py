"""Layered configuration: packaged defaults, deep-merged with an optional override file."""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator


class ConfigError(Exception):
    """Raised for YAML errors, unknown keys, bad types and a missing explicit path."""


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OutputConfig(_Section):
    dir: Path
    open_single: bool


class ServersConfig(_Section):
    simbad: list[str]
    vizier: list[str]


class CacheConfig(_Section):
    dir: Path
    empty_expiry_days: float
    expiry_days: dict[str, float | None]

    @field_validator("dir")
    @classmethod
    def _expand_home(cls, v: Path) -> Path:
        return v.expanduser()


class SourcesConfig(_Section):
    max_workers: int
    timeout_s: float
    timeout_overrides: dict[str, float] = {}
    enabled: dict[str, bool]
    radius_arcsec: dict[str, float]
    vizier_catalogs: dict[str, str]
    koa_instruments: list[str]


class ResolveConfig(_Section):
    match_radius_arcsec: float
    coord_search_arcsec: float
    pair_radius_arcsec: float
    pair_max_delta_g: float
    max_mag_mismatch: float


class ParametersConfig(_Section):
    disagreement_sigma: float
    disagreement_fraction: float
    disagreement_absolute: dict[str, float]


class FlagsConfig(_Section):
    ruwe_max: float
    contamination_max: float


class PlotsConfig(_Section):
    enabled: bool
    max_sectors: int
    lightcurve_timeout_s: float
    cutout_arcmin: float
    sky_survey: str
    sky_fallback_survey: str
    neighbor_max_delta_g: float
    tess_pixel_arcsec: float
    cmd_background_rows: int
    cmd_background_timeout_s: float
    periodogram_min_period_d: float
    periodogram_max_period_d: float
    period_merge_tolerance: float


class LiteratureConfig(_Section):
    max_records: int
    doctypes: list[str]
    most_cited: int
    most_recent: int


class LLMConfig(_Section):
    enabled: bool
    model: str
    effort: str
    max_tokens: int
    server_fallback: bool
    timeout_s: float
    max_retries: int


class Config(_Section):
    output: OutputConfig
    servers: ServersConfig
    cache: CacheConfig
    sources: SourcesConfig
    resolve: ResolveConfig
    parameters: ParametersConfig
    flags: FlagsConfig
    plots: PlotsConfig
    literature: LiteratureConfig
    llm: LLMConfig


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_yaml(text: str, origin: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{origin}: invalid YAML: {' '.join(str(exc).split())}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{origin}: top level must be a mapping")
    return data


def load_config(path: Path | None = None, cwd: Path | None = None) -> Config:
    """Load defaults, then deep-merge `path`, else `<cwd>/config.yaml` if present."""
    defaults = _read_yaml(
        files("star_report").joinpath("config.yaml").read_text(encoding="utf-8"), "defaults"
    )
    if path is not None:
        source: Path | None = Path(path)
    else:
        local = (cwd if cwd is not None else Path.cwd()) / "config.yaml"
        source = local if local.exists() else None
    merged = defaults
    if source is not None:
        try:
            text = source.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ConfigError(f"config file not found: {source}") from exc
        except OSError as exc:
            raise ConfigError(f"cannot read config file {source}: {exc}") from exc
        merged = _deep_merge(defaults, _read_yaml(text, str(source)))
    try:
        return Config.model_validate(merged)
    except ValidationError as exc:
        parts = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise ConfigError("invalid configuration: " + "; ".join(parts)) from exc


def ads_token() -> str | None:
    """ADS_DEV_KEY from the environment; empty counts as unset."""
    return os.environ.get("ADS_DEV_KEY") or None
