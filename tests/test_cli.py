"""Argument handling, exit codes and --open; run_target is monkeypatched."""

from __future__ import annotations

import pytest

from star_report import cli, pipeline
from star_report.pipeline import TargetOutcome


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no stray ./config.yaml
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url))
    opened.clear()


opened: list[str] = []


def _ok(target, cfg, opts, cache, console, deps=None):
    return TargetOutcome(target, "ok", report_path=opts.out_dir / "x" / "report.html")


def test_targets_file(tmp_path):
    p = tmp_path / "t.txt"
    p.write_text("# list\nHD 209458\n\n  TOI-700  \n")
    assert cli.read_targets_file(p) == ["HD 209458", "TOI-700"]


def test_exit_codes(monkeypatch, tmp_path):
    ran = []

    def fake(target, *a, **k):
        ran.append(target)
        if target == "bad":
            return TargetOutcome(target, "unresolved", message="Could not resolve 'bad'.")
        if target == "boom":
            raise RuntimeError("kaboom")
        return _ok(target, *a, **k)

    monkeypatch.setattr(pipeline, "run_target", fake)
    assert cli.main(["a", "b", "--no-open"]) == 0
    assert cli.main(["a", "bad", "--no-open"]) == 1
    ran.clear()
    assert cli.main(["boom", "b", "--no-open"]) == 1
    assert ran == ["boom", "b"]
    bad = tmp_path / "bad.yaml"
    bad.write_text("output: [unclosed")
    assert cli.main(["a", "--config", str(bad)]) == 2
    bad.write_text("sources: {max_workers: 0}\n")
    ran.clear()
    assert cli.main(["a", "--config", str(bad)]) == 2 and ran == []
    with pytest.raises(SystemExit) as e:
        cli.main([])
    assert e.value.code == 2
    assert cli.main(["--from-file", str(tmp_path / "missing.txt")]) == 2


def test_open_defaults(monkeypatch):
    monkeypatch.setattr(pipeline, "run_target", _ok)
    cli.main(["a"])
    assert len(opened) == 1
    opened.clear()
    cli.main(["a", "b"])
    assert opened == []
    cli.main(["a", "--no-open"])
    assert opened == []
    cli.main(["a", "b", "--open"])
    assert len(opened) == 2


def test_flags_reach_config(monkeypatch, tmp_path):
    seen = {}

    def fake(target, cfg, opts, cache, console, deps=None):
        seen.update(llm=cfg.llm.enabled, plots=cfg.plots.enabled, out=opts.out_dir,
                    refresh_opts=opts.refresh, refresh_cache=cache.refresh)
        return _ok(target, cfg, opts, cache, console)

    monkeypatch.setattr(pipeline, "run_target", fake)
    cli.main(["a", "--no-llm", "--no-plots", "--refresh", "--out", str(tmp_path / "o"), "--no-open"])
    assert seen == {"llm": False, "plots": False, "out": tmp_path / "o",
                    "refresh_opts": True, "refresh_cache": True}
