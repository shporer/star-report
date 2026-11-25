"""PDF printing through a headless Chromium browser; a fake browser script stands in for it."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

import pytest
from conftest import make_report

from star_report.config import load_config
from star_report.render import pdf
from star_report.render.html import write_outputs

pytestmark = pytest.mark.pdf_browser  # find_browser is real here; the browser is fake

FAKE_BROWSER = '''
import json, pathlib, sys, time, urllib.parse
args = sys.argv[1:]
pathlib.Path({log!r}).write_text(json.dumps(args))
if {behaviour!r} == "hang":
    time.sleep(30)
if {behaviour!r} == "fail":
    sys.exit(3)
out = next(a.split("=", 1)[1] for a in args if a.startswith("--print-to-pdf="))
html = pathlib.Path(urllib.parse.urlparse(args[-1]).path).read_text()
pathlib.Path(out).write_bytes(b"%PDF-1.4 fake " + html.encode())
'''


def fake_browser(tmp_path: Path, behaviour: str = "ok", name: str = "fake-chrome"):
    """An executable stand-in for Chrome; returns its path and a reader for its arguments."""
    log = tmp_path / f"{name}-args.json"
    script = tmp_path / name
    script.write_text(f"#!{sys.executable}\n" + FAKE_BROWSER.format(log=str(log),
                                                                    behaviour=behaviour))
    script.chmod(0o755)
    return script, lambda: json.loads(log.read_text())


def _setup(tmp_path, browser):
    cfg = load_config(cwd=tmp_path)
    cfg.output.pdf_browsers = [str(browser)]
    report = make_report()
    out = tmp_path / "out"
    write_outputs(report, out)
    return cfg, report, out


def test_prints_report_pdf_from_the_print_version(tmp_path):
    browser, _ = fake_browser(tmp_path)
    cfg, report, out = _setup(tmp_path, browser)
    path, reason = pdf.write_pdf(report, out, cfg)
    assert (path, reason) == (out / "report.pdf", None)
    printed = path.read_bytes()
    assert printed.startswith(b"%PDF")
    assert b'<details id="sources" open' in printed and b"size: letter" in printed


def test_browser_runs_headless_with_a_throwaway_profile(tmp_path):
    browser, browser_args = fake_browser(tmp_path)
    cfg, report, out = _setup(tmp_path, browser)
    pdf.write_pdf(report, out, cfg)
    args = browser_args()
    assert "--headless=new" in args and "--no-pdf-header-footer" in args
    profile = next(a.split("=", 1)[1] for a in args if a.startswith("--user-data-dir="))
    assert Path(profile).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
    assert not Path(profile).exists()  # removed afterwards
    assert args[-1].startswith("file://")


def test_no_browser_found_removes_a_stale_pdf(tmp_path):
    cfg, report, out = _setup(tmp_path, tmp_path / "no-such-browser")
    (out / "report.pdf").write_bytes(b"%PDF old")
    path, reason = pdf.write_pdf(report, out, cfg)
    assert path is None and reason.startswith("no Chrome, Chromium or Edge found")
    assert not (out / "report.pdf").exists()


def test_failing_browser_leaves_no_pdf(tmp_path):
    browser, _ = fake_browser(tmp_path, "fail")
    cfg, report, out = _setup(tmp_path, browser)
    path, reason = pdf.write_pdf(report, out, cfg)
    assert path is None and "exit 3" in reason
    assert not (out / "report.pdf").exists()


def test_hanging_browser_is_stopped_at_the_deadline(tmp_path):
    browser, _ = fake_browser(tmp_path, "hang")
    cfg, report, out = _setup(tmp_path, browser)
    cfg.output.pdf_timeout_s = 0.5
    t0 = time.monotonic()
    path, reason = pdf.write_pdf(report, out, cfg)
    assert time.monotonic() - t0 < 5
    assert path is None and reason == "the browser did not finish within 0.5 s"


def test_find_browser_takes_the_first_candidate_that_exists(tmp_path, monkeypatch):
    on_path, _ = fake_browser(tmp_path, name="chromium")
    explicit, _ = fake_browser(tmp_path, name="my-chrome")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert pdf.find_browser([str(tmp_path / "missing"), "chromium", str(explicit)]) == str(on_path)
    assert pdf.find_browser([str(explicit), "chromium"]) == str(explicit)
    assert pdf.find_browser([str(tmp_path / "missing"), "not-on-path"]) is None
