import socket
from pathlib import Path

import pytest

from star_report.config import ConfigError, ads_token, load_config


def test_defaults(tmp_path):
    cfg = load_config(cwd=tmp_path)
    assert (cfg.llm.model, cfg.llm.effort) == ("claude-opus-5-5", "high")
    assert (cfg.llm.timeout_s, cfg.llm.max_retries) == (300, 1)
    assert (cfg.sources.max_workers, cfg.sources.timeout_s) == (8, 30)
    assert cfg.sources.timeout_overrides == {"mast": 90}
    assert cfg.cache.expiry_days["llm"] is None and cfg.cache.expiry_days["simbad"] == 30
    assert (cfg.literature.most_cited, cfg.literature.most_recent) == (15, 10)
    assert cfg.servers.simbad == ["simbad.cds.unistra.fr", "simbad.harvard.edu"]
    assert cfg.resolve.max_mag_mismatch == 3.0
    assert (cfg.plots.lightcurve_timeout_s, cfg.plots.cmd_background_timeout_s) == (120, 120)
    assert "~" not in str(cfg.cache.dir)


def test_local_file_overrides_only_its_keys(tmp_path):
    (tmp_path / "config.yaml").write_text("llm:\n  effort: medium\n")
    cfg = load_config(cwd=tmp_path)
    assert (cfg.llm.effort, cfg.llm.model) == ("medium", "claude-opus-5-5")


def test_explicit_path_beats_local_file(tmp_path):
    (tmp_path / "config.yaml").write_text("llm:\n  effort: low\n")
    other = tmp_path / "other.yaml"
    other.write_text("llm:\n  effort: max\n")
    assert load_config(path=other, cwd=tmp_path).llm.effort == "max"


def test_unknown_key_is_config_error(tmp_path):
    (tmp_path / "config.yaml").write_text("llm:\n  modle: x\n")
    with pytest.raises(ConfigError, match="modle"):
        load_config(cwd=tmp_path)


@pytest.mark.parametrize("override,key", [
    ("sources: {max_workers: 0}", "sources.max_workers"),
    ("sources: {max_workers: -2}", "sources.max_workers"),
    ("sources: {timeout_s: 0}", "sources.timeout_s"),
    ("sources: {timeout_s: -5}", "sources.timeout_s"),
    ("sources: {timeout_s: .nan}", "sources.timeout_s"),
    ("sources: {timeout_s: .inf}", "sources.timeout_s"),
    ("sources: {timeout_overrides: {mast: -90}}", "sources.timeout_overrides.mast"),
    ("plots: {lightcurve_timeout_s: 0}", "plots.lightcurve_timeout_s"),
    ("plots: {cmd_background_timeout_s: -1}", "plots.cmd_background_timeout_s"),
    ("llm: {timeout_s: -300}", "llm.timeout_s"),
    ("llm: {max_retries: -1}", "llm.max_retries"),
    ("llm: {claude_code: {timeout_s: 0}}", "llm.claude_code.timeout_s"),
    ("llm: {claude_code: {command: ''}}", "llm.claude_code.command"),
    ("llm: {backend: openai}", "llm.backend"),
])
def test_non_positive_workers_and_timeouts_are_config_errors(tmp_path, override, key):
    (tmp_path / "config.yaml").write_text(override + "\n")
    with pytest.raises(ConfigError, match=f"invalid configuration: {key}: "):
        load_config(cwd=tmp_path)


def test_positive_timeouts_and_zero_retries_are_valid(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "sources: {max_workers: 1, timeout_s: 0.5, timeout_overrides: {mast: 2}}\n"
        "llm: {max_retries: 0}\n")
    cfg = load_config(cwd=tmp_path)
    assert (cfg.sources.max_workers, cfg.sources.timeout_s, cfg.llm.max_retries) == (1, 0.5, 0)


def test_llm_backend_defaults_and_override(tmp_path):
    cfg = load_config(cwd=tmp_path)
    assert (cfg.llm.backend, cfg.llm.claude_code.command, cfg.llm.claude_code.timeout_s) == \
        ("api", "claude", 600)
    (tmp_path / "config.yaml").write_text(
        "llm: {backend: claude-code, claude_code: {command: ~/bin/claude}}\n")
    cfg = load_config(cwd=tmp_path)
    assert cfg.llm.backend == "claude-code"
    assert cfg.llm.claude_code.command == str(Path.home() / "bin" / "claude")


def test_bad_yaml_is_config_error(tmp_path):
    (tmp_path / "config.yaml").write_text("llm: [")
    with pytest.raises(ConfigError):
        load_config(cwd=tmp_path)


def test_missing_explicit_path_is_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(path=tmp_path / "nope.yaml", cwd=tmp_path)


def test_ads_token(monkeypatch):
    monkeypatch.setenv("ADS_DEV_KEY", "")
    assert ads_token() is None
    monkeypatch.setenv("ADS_DEV_KEY", "abc")
    assert ads_token() == "abc"
    monkeypatch.delenv("ADS_DEV_KEY")
    assert ads_token() is None


def test_network_blocked_by_default():
    with pytest.raises(Exception):
        socket.create_connection(("example.com", 80), timeout=1)
