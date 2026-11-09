import socket

import pytest

from star_report.config import ConfigError, ads_token, load_config


def test_defaults(tmp_path):
    cfg = load_config(cwd=tmp_path)
    assert (cfg.llm.model, cfg.llm.effort) == ("claude-opus-5-5", "high")
    assert (cfg.sources.max_workers, cfg.sources.timeout_s) == (8, 30)
    assert cfg.cache.expiry_days["cmd_background"] is None and cfg.cache.expiry_days["simbad"] == 30
    assert (cfg.literature.most_cited, cfg.literature.most_recent) == (15, 10)
    assert cfg.servers.simbad == ["simbad.cds.unistra.fr", "simbad.harvard.edu"]
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
