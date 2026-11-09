"""Self-contained HTML report and data.json writer."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

from jinja2 import Environment, PackageLoader
from markupsafe import Markup, escape

from star_report.derive import description_line, display_name
from star_report.models import BIBCODE_RE, Report

STATUS_LABELS = {
    "ok": "ok",
    "empty": "no data found",
    "error": "unavailable",
    "timeout": "timed out",
    "skipped": "skipped",
}

_ADS_URL = "https://ui.adsabs.harvard.edu/abs/{bibcode}/abstract"


def link_citations(text: str) -> Markup:
    """Escape text and turn each [bibcode] into an ADS link."""
    out: list[str] = []
    pos = 0
    for m in BIBCODE_RE.finditer(text):
        out.append(str(escape(text[pos:m.start()])))
        code = m.group(1)
        out.append(f'<a href="{_ADS_URL.format(bibcode=escape(code))}">{escape(code)}</a>')
        pos = m.end()
    out.append(str(escape(text[pos:])))
    return Markup("".join(out))


def sexagesimal(ra: float, dec: float) -> tuple[str, str]:
    h = (ra % 360.0) / 15.0
    hh = int(h)
    mm = int((h - hh) * 60)
    ss = ((h - hh) * 60 - mm) * 60
    if round(ss, 2) >= 60:
        ss, mm = 0.0, mm + 1
    if mm == 60:
        mm, hh = 0, (hh + 1) % 24
    sign = "-" if dec < 0 else "+"
    a = abs(dec)
    dd = int(a)
    dm = int((a - dd) * 60)
    ds = ((a - dd) * 60 - dm) * 60
    if round(ds, 1) >= 60:
        ds, dm = 0.0, dm + 1
    if dm == 60:
        dm, dd = 0, dd + 1
    return f"{hh:02d}h {mm:02d}m {ss:05.2f}s", f"{sign}{dd:02d}° {dm:02d}′ {ds:04.1f}″"


_EPOCH_LABELS = {"gaia_dr3": "Gaia DR3", "simbad": "SIMBAD", "tic": "TIC"}


def epoch_label(report: Report) -> str:
    pos = report.identity.position
    return f"J{pos.epoch:g}" if pos.epoch != 2016.0 else "J2016.0"


def _epoch_text(report: Report) -> str:
    pos = report.identity.position
    return f"{epoch_label(report)} ({_EPOCH_LABELS[pos.source]})"


def _fmt_cell(value: float | None, err: float | None, decimals: int) -> str:
    if value is None:
        return ""
    text = f"{value:.{decimals}f}"
    if err is not None:
        text += f" ± {err:.{decimals}f}"
    return text


_env = Environment(loader=PackageLoader("star_report", "render/templates"), autoescape=True)
_env.filters["cite"] = link_citations
_env.globals["fmt_cell"] = _fmt_cell
_env.globals["STATUS_LABELS"] = STATUS_LABELS


def _embed_plots(report: Report, base_dir: Path) -> dict[str, str]:
    images: dict[str, str] = {}
    for name, rel in report.plots.items():
        path = Path(base_dir) / rel
        if path.suffix.lower() != ".png" or not path.is_file():
            continue
        images[name] = base64.b64encode(path.read_bytes()).decode("ascii")
    return images


def _strip_prefix(prefix: str, value: str) -> str:
    return re.sub(rf"^{re.escape(prefix)}\s*", "", value)


def render_html(report: Report, base_dir: Path) -> str:
    ident = report.identity
    ids: list[tuple[str, str]] = []
    if ident.tic_id is not None:
        ids.append(("TIC", f"TIC {ident.tic_id}"))
    if ident.gaia_dr3_id:
        ids.append(("Gaia DR3", f"Gaia DR3 {ident.gaia_dr3_id}"))
    for key in ("HD", "HIP"):
        if key in ident.other_ids:
            ids.append((key, f"{key} {_strip_prefix(key, ident.other_ids[key])}"))
    if "2MASS" in ident.other_ids:
        ids.append(("2MASS", f"2MASS {_strip_prefix('2MASS', ident.other_ids['2MASS'])}"))
    ra_s, dec_s = sexagesimal(ident.position.ra, ident.position.dec)
    return _env.get_template("report.html.j2").render(
        report=report,
        name=display_name(ident.main_name),
        ids=ids,
        ra_s=ra_s,
        dec_s=dec_s,
        epoch=_epoch_text(report),
        description=description_line(report.results, report.parameters),
        plot_images=_embed_plots(report, base_dir),
    )


def write_outputs(report: Report, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "report.html"
    html_path.write_text(render_html(report, out_dir), encoding="utf-8")
    (out_dir / "data.json").write_text(
        json.dumps(report.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    return html_path
