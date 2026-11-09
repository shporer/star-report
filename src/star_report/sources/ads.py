"""NASA ADS: papers that mention the target, selected as most cited plus most recent."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from star_report.derive import display_name
from star_report.models import Paper
from star_report.sources.base import Source, SourceContext

URL = "https://api.adsabs.harvard.edu/v1/search/query"
OBJECTS_URL = "https://api.adsabs.harvard.edu/v1/objects/query"
FIELDS = "bibcode,title,year,pubdate,author,citation_count,abstract,doctype"
MAX_AUTHORS = 3


def _escape(name: str) -> str:
    return name.replace('"', '\\"')


def query_string(translated: str, doctypes: Sequence[str]) -> str:
    """The search query: the translated object query restricted to the given doctypes."""
    return f'({translated}) doctype:({" OR ".join(doctypes)})'


def select_papers(papers: list[Paper], most_cited: int, most_recent: int) -> list[Paper]:
    """The most cited papers, then the most recent ones not already chosen."""
    cited = sorted(papers, key=lambda p: (-p.citation_count, p.bibcode))[:most_cited]
    chosen = {p.bibcode for p in cited}
    by_bibcode = sorted(papers, key=lambda p: p.bibcode)
    recent = sorted(by_bibcode, key=lambda p: p.pubdate or "", reverse=True)[:most_recent]
    return cited + [p for p in recent if p.bibcode not in chosen]


def _reset_text(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return "an unknown time"


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _paper(doc: Any) -> Paper | None:
    if not isinstance(doc, dict) or not isinstance(doc.get("bibcode"), str):
        return None
    title = doc.get("title")
    if isinstance(title, list):
        title = title[0] if title else None
    if not isinstance(title, str):
        title = ""
    authors = doc.get("author")
    authors = [a for a in authors if isinstance(a, str)][:MAX_AUTHORS] if isinstance(authors, list) else []
    return Paper(
        bibcode=doc["bibcode"],
        title=title,
        year=_int(doc.get("year")),
        pubdate=doc["pubdate"] if isinstance(doc.get("pubdate"), str) else None,
        authors=authors,
        citation_count=_int(doc.get("citation_count")) or 0,
        abstract=doc["abstract"] if isinstance(doc.get("abstract"), str) else None,
        doctype=doc["doctype"] if isinstance(doc.get("doctype"), str) else None,
    )


class Ads(Source):
    name = "ads"
    group = "ads"
    service = "NASA ADS search API"

    def skip_reason(self, ctx: SourceContext) -> str | None:
        return "ADS_DEV_KEY not set" if ctx.ads_token is None else None

    def query(self, ctx: SourceContext) -> dict:
        lit = ctx.config.literature
        return {"name": display_name(ctx.identity.main_name), "doctypes": list(lit.doctypes),
                "rows": lit.max_records}

    @staticmethod
    def _check_rate_limit(response: Any, payload_ok: bool = False) -> None:
        remaining = response.headers.get("X-RateLimit-Remaining")
        if response.status_code == 429 or (
                str(remaining) == "0" and (response.status_code != 200 or not payload_ok)):
            reset = _reset_text(response.headers.get("X-RateLimit-Reset"))
            raise RuntimeError(f"ADS rate limit reached; resets at {reset}")

    def _translate(self, name: str, ctx: SourceContext, headers: dict) -> str:
        """ADS's own object query for a name; the abstract-phrase search when that fails."""
        fallback = f'=abs:"{_escape(name)}"'
        try:
            response = ctx.session.post(
                OBJECTS_URL, json={"query": [f'object:"{_escape(name)}"']},
                timeout=self.timeout_s(ctx.config),
                headers={**headers, "Content-Type": "application/json"})
        except Exception:  # noqa: BLE001 - any failure of the helper endpoint falls back
            return fallback
        if response.status_code == 429:
            self._check_rate_limit(response)
        if response.status_code != 200:
            return fallback
        try:
            translated = response.json()["query"]
        except (ValueError, KeyError, TypeError):
            return fallback
        return translated if isinstance(translated, str) and translated.strip() else fallback

    def fetch(self, ctx: SourceContext) -> Any:
        lit = ctx.config.literature
        headers = {"Authorization": f"Bearer {ctx.ads_token}"}
        translated = self._translate(display_name(ctx.identity.main_name), ctx, headers)
        q = query_string(translated, lit.doctypes)
        response = ctx.session.get(
            URL, params={"q": q, "fl": FIELDS, "rows": lit.max_records,
                         "sort": "citation_count desc"},
            timeout=self.timeout_s(ctx.config), headers=headers)
        self._check_rate_limit(response, payload_ok=True)
        response.raise_for_status()
        payload = response.json()
        self._check_rate_limit(response, payload_ok=bool(self._docs(payload)))
        return {"q": q, "response": payload.get("response") if isinstance(payload, dict) else None}

    @staticmethod
    def _docs(raw: Any) -> list:
        body = raw.get("response") if isinstance(raw, dict) else None
        docs = body.get("docs") if isinstance(body, dict) else None
        return docs if isinstance(docs, list) else []

    def parse(self, raw: Any, ctx: SourceContext) -> dict:
        papers = [p for p in map(_paper, self._docs(raw)) if p is not None]
        if not papers:
            return {}
        lit = ctx.config.literature
        found = _int(raw["response"].get("numFound"))
        chosen = select_papers(papers, lit.most_cited, lit.most_recent)
        return {"num_found": found if found is not None else len(papers),
                "papers": [p.model_dump() for p in chosen]}
