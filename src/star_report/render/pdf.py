"""The report as a PDF, printed by a headless Chromium browser (Chrome, Chromium or Edge)."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from star_report.config import Config
from star_report.models import Report
from star_report.render.html import render_html

PDF_NAME = "report.pdf"


def find_browser(candidates: list[str]) -> str | None:
    """The first candidate that is an executable file, or a command found on PATH."""
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_absolute():
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
        elif found := shutil.which(candidate):
            return found
    return None


def _stop(proc: subprocess.Popen) -> None:
    """Kill the browser and the helper processes it started (its own process group)."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except OSError:
        pass
    try:
        proc.communicate(timeout=5)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass


def write_pdf(report: Report, out_dir: Path, cfg: Config) -> tuple[Path | None, str | None]:
    """Print the report to out_dir/report.pdf: (its path, None), or (None, why it was not written).

    The browser prints a print version of the HTML (collapsed sections expanded, paper size from
    output.pdf_paper), with a throwaway profile so a running browser is not touched, and is
    stopped at output.pdf_timeout_s. A PDF from an earlier run is removed first, so a PDF never
    disagrees with the HTML beside it.
    """
    out_dir = Path(out_dir)
    target = out_dir / PDF_NAME
    target.unlink(missing_ok=True)
    browser = find_browser(list(cfg.output.pdf_browsers))
    if browser is None:
        return None, "no Chrome, Chromium or Edge found (list one in output.pdf_browsers)"
    html = render_html(report, out_dir, for_print=True, paper=cfg.output.pdf_paper)
    timeout = float(cfg.output.pdf_timeout_s)
    with tempfile.TemporaryDirectory(prefix="star-report-pdf-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        page, printed = work / "report.html", work / PDF_NAME
        page.write_text(html, encoding="utf-8")
        argv = [browser, "--headless=new", "--disable-gpu", "--no-first-run",
                "--no-default-browser-check", "--no-pdf-header-footer",
                f"--user-data-dir={work / 'profile'}", f"--print-to-pdf={printed}",
                page.resolve().as_uri()]
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, text=True, errors="replace",
                                    start_new_session=True)
        except OSError as exc:
            return None, f"the browser could not be started ({exc})"[:200]
        try:
            _, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _stop(proc)
            return None, f"the browser did not finish within {timeout:g} s"
        except BaseException:  # Ctrl-C: do not leave the browser running
            _stop(proc)
            raise
        if proc.returncode != 0 or not printed.is_file() or printed.stat().st_size == 0:
            last = (stderr or "").strip().splitlines()[-1:]
            detail = f": {last[0][:150]}" if last else ""
            return None, f"the browser failed (exit {proc.returncode}){detail}"
        shutil.move(str(printed), str(target))
    return target, None
