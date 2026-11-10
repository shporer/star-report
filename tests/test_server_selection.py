"""Server selection: the first SIMBAD/VizieR host that answers a small query is used, once per run."""

import io
import threading
import time
from urllib.parse import urlsplit

import pytest
import requests

from star_report.config import ConfigError, ServerProbe, load_config
from star_report.sources import base, simbad, vizier
from star_report.sources.base import pick_server

# Answers as (status, Content-Type, body[, extra headers]). Headers are as the hosts sent them on
# 2026-11-10; bodies are shortened.
VOTABLE_BODY = (b'<?xml version="1.0" encoding="utf-8"?>\n'
                b'<VOTABLE version="1.3" xmlns="http://www.ivoa.net/xml/VOTable/v1.3">\n')
VOTABLE = (200, "application/x-votable+xml;charset=UTF-8", VOTABLE_BODY)   # simbad.harvard.edu
CDS_404 = (404, "text/html", b'<!doctype html>\n<html lang="en">\n<head>\n  <meta charset="utf-8">\n')

PROBE = ServerProbe(path="/tap/sync", params={"QUERY": "SELECT TOP 1 x FROM t", "MAXREC": 1},
                    content_types=["application/x-votable+xml"])

RELEASE = threading.Event()  # set at teardown so hanging fakes return


class FakeNet:
    """Stands in for the network below requests: each host answers as `answers` says."""

    def __init__(self, answers, delay=0.0):
        self.answers = answers
        self.delay = delay
        self.requests = []  # (PreparedRequest, adapter send kwargs, sent from a daemon thread)

    def send(self, request, **kwargs):
        self.requests.append((request, kwargs, threading.current_thread().daemon))
        time.sleep(self.delay)
        answer = self.answers[urlsplit(request.url).hostname]
        if answer == "hang":
            RELEASE.wait(10)
            raise requests.exceptions.ConnectionError("released at teardown")
        if isinstance(answer, BaseException):
            raise answer
        status, content_type, body, *extra = answer
        resp = requests.Response()
        resp.status_code, resp.url, resp.request = status, request.url, request
        resp.headers["Content-Type"] = content_type
        resp.headers.update(extra[0] if extra else {})
        resp.raw = io.BytesIO(body)
        return resp

    def hosts(self):
        return [urlsplit(r.url).hostname for r, _, _ in self.requests]


@pytest.fixture(autouse=True)
def _new_run(monkeypatch):
    """Each test is a fresh run: no host chosen yet."""
    monkeypatch.setattr(base, "_selections", {})
    RELEASE.clear()
    yield
    RELEASE.set()


@pytest.fixture
def net(monkeypatch):
    def install(answers, delay=0.0):
        fake = FakeNet(answers, delay)
        monkeypatch.setattr(requests.adapters.HTTPAdapter, "send",
                            lambda adapter, request, **kwargs: fake.send(request, **kwargs))
        return fake
    return install


@pytest.mark.parametrize("content_type", [
    "application/x-votable+xml;charset=UTF-8",    # simbad.harvard.edu
    "application/x-votable+xml",                  # vizier.cfa.harvard.edu
    "Application/X-VOTable+XML; charset=utf-8",   # media types are case-insensitive
])
def test_first_host_answering_200_with_an_expected_type_is_used(net, content_type):
    fake = net({"a.test": (200, content_type, VOTABLE_BODY), "b.test": VOTABLE})
    assert pick_server(["a.test", "b.test"], PROBE, 0.3) == "a.test"
    assert fake.hosts() == ["a.test"]  # later hosts are not asked


NOT_SERVING = {
    "404 html error page": CDS_404,
    "200 with the wrong type": (200, "text/html; charset=utf-8", b"<html>Down for maintenance</html>"),
    "200 without a type": (200, "", VOTABLE_BODY),
    "error status with a votable": (503, "application/x-votable+xml",
                                    b'<?xml version="1.0"?><VOTABLE><INFO name="QUERY_STATUS" '
                                    b'value="ERROR">unavailable</INFO></VOTABLE>'),
    "redirect": (302, "text/html", b"", {"Location": "https://b.test/tap/sync"}),
    "read timeout": requests.exceptions.ReadTimeout("Read timed out. (read timeout=0.3)"),
    "connection refused": requests.exceptions.ConnectionError(
        "HTTPSConnectionPool(host='a.test', port=443): Max retries exceeded "
        "(Caused by NewConnectionError('[Errno 61] Connection refused'))"),
    "no answer by the deadline": "hang",
}


@pytest.mark.parametrize("answer", NOT_SERVING.values(), ids=NOT_SERVING.keys())
def test_host_that_does_not_serve_the_query_is_skipped(net, answer):
    fake = net({"a.test": answer, "b.test": VOTABLE})
    t0 = time.monotonic()
    assert pick_server(["a.test", "b.test"], PROBE, 0.3) == "b.test"
    assert time.monotonic() - t0 < 1.5
    assert fake.hosts() == ["a.test", "b.test"]


def test_hosts_are_tried_in_list_order(net):
    fake = net({"a.test": CDS_404, "b.test": CDS_404, "c.test": VOTABLE, "d.test": VOTABLE})
    assert pick_server(["a.test", "b.test", "c.test", "d.test"], PROBE, 0.3) == "c.test"
    assert fake.hosts() == ["a.test", "b.test", "c.test"]


def test_probe_is_one_get_of_the_configured_query_in_a_daemon_thread(net):
    fake = net({"a.test": VOTABLE})
    pick_server(["a.test"], PROBE, 0.3)
    [(request, kwargs, from_daemon)] = fake.requests
    assert request.method == "GET"
    assert request.url == "https://a.test/tap/sync?QUERY=SELECT+TOP+1+x+FROM+t&MAXREC=1"
    assert request.headers["User-Agent"].startswith("star-report/")
    assert kwargs["timeout"] == 0.3  # so the thread ends even after the deadline has passed
    assert from_daemon


def test_choice_is_kept_for_the_run(net):
    fake = net({"a.test": CDS_404, "b.test": VOTABLE})
    assert [pick_server(["a.test", "b.test"], PROBE, 0.3) for _ in range(3)] == ["b.test"] * 3
    assert fake.hosts() == ["a.test", "b.test"]


def test_when_no_host_answers_the_first_is_used_without_asking_again(net):
    fake = net({"a.test": CDS_404,
                "b.test": requests.exceptions.ConnectionError("[Errno 61] Connection refused")})
    assert pick_server(["a.test", "b.test"], PROBE, 0.3) == "a.test"
    assert pick_server(["a.test", "b.test"], PROBE, 0.3) == "a.test"
    assert fake.hosts() == ["a.test", "b.test"]


def test_concurrent_first_calls_share_one_round_of_probes(net):
    fake = net({"a.test": CDS_404, "b.test": VOTABLE}, delay=0.1)
    start = threading.Barrier(6)
    chosen = []

    def call():
        start.wait()
        chosen.append(pick_server(["a.test", "b.test"], PROBE, 1.0))

    threads = [threading.Thread(target=call, daemon=True) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert chosen == ["b.test"] * 6
    assert fake.hosts() == ["a.test", "b.test"]


def test_a_slow_selection_does_not_hold_up_another_service(net):
    fake = net({"slow.test": "hang", "b.test": VOTABLE})
    other = PROBE.model_copy(update={"path": "/viz-bin/votable"})
    slow = threading.Thread(target=pick_server, args=(["slow.test"], PROBE, 1.0), daemon=True)
    slow.start()
    deadline = time.monotonic() + 2
    while "slow.test" not in fake.hosts() and time.monotonic() < deadline:
        time.sleep(0.01)
    t0 = time.monotonic()
    assert pick_server(["b.test"], other, 1.0) == "b.test"
    assert time.monotonic() - t0 < 0.5
    slow.join(3)


def test_cds_error_pages_send_simbad_and_vizier_to_the_mirrors(net, tmp_path):
    """2026-11-10: CDS accepted connections but answered every query with a 404 HTML page."""
    cfg = load_config(cwd=tmp_path)
    cfg.servers.simbad = ["simbad-cds.test", "simbad-mirror.test"]
    cfg.servers.vizier = ["vizier-cds.test", "vizier-mirror.test"]
    cfg.servers.probe_timeout_s = 0.3
    fake = net({"simbad-cds.test": CDS_404, "simbad-mirror.test": VOTABLE,
                "vizier-cds.test": CDS_404,
                "vizier-mirror.test": (200, "application/x-votable+xml", VOTABLE_BODY)})
    assert simbad._url(cfg) == "https://simbad-mirror.test/simbad/sim-tap"
    assert vizier.vizier_client(cfg, ["HIP"]).VIZIER_SERVER == "vizier-mirror.test"
    probes = cfg.servers.probes
    assert [(urlsplit(r.url).hostname, urlsplit(r.url).path) for r, _, _ in fake.requests] == [
        ("simbad-cds.test", probes.simbad.path), ("simbad-mirror.test", probes.simbad.path),
        ("vizier-cds.test", probes.vizier.path), ("vizier-mirror.test", probes.vizier.path)]
    assert {kwargs["timeout"] for _, kwargs, _ in fake.requests} == {0.3}


# ---- configuration ----


@pytest.mark.parametrize("override,key", [
    ("servers: {simbad: ['https://simbad.harvard.edu']}", "servers.simbad.0"),
    ("servers: {vizier: [vizier.cds.unistra.fr, 'vizier.cfa.harvard.edu/viz-bin']}", "servers.vizier.1"),
    ("servers: {simbad: ['simbad.harvard.edu?x=1']}", "servers.simbad.0"),
    ("servers: {probe_timeout_s: 0}", "servers.probe_timeout_s"),
    ("servers: {probe_timeout_s: -5}", "servers.probe_timeout_s"),
    ("servers: {probes: {simbad: {path: simbad/sim-tap/sync}}}", "servers.probes.simbad.path"),
    ("servers: {probes: {vizier: {path: '/viz-bin/votable?-source=x'}}}", "servers.probes.vizier.path"),
    ("servers: {probes: {vizier: {content_types: []}}}", "servers.probes.vizier.content_types"),
])
def test_bad_server_settings_are_config_errors(tmp_path, override, key):
    (tmp_path / "config.yaml").write_text(override + "\n")
    with pytest.raises(ConfigError, match=f"invalid configuration: {key}: "):
        load_config(cwd=tmp_path)


def test_local_host_list_still_loads(tmp_path):
    """The stopgap for a CDS outage: a local config that lists the Harvard mirrors first."""
    (tmp_path / "config.yaml").write_text(
        "servers:\n  simbad: [simbad.harvard.edu, simbad.cds.unistra.fr]\n"
        "  vizier: ['vizier.cfa.harvard.edu:443', vizier.cds.unistra.fr]\n")
    cfg = load_config(cwd=tmp_path)
    assert cfg.servers.simbad == ["simbad.harvard.edu", "simbad.cds.unistra.fr"]
    assert cfg.servers.vizier == ["vizier.cfa.harvard.edu:443", "vizier.cds.unistra.fr"]
