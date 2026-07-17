"""Unit tests for the Prism Central v4 connection wrapper.

The httpx client is injected as a fake, so no network is touched. Proves the two
things every hand-rolled v4 client gets wrong are handled here: ETag capture +
If-Match replay, and $page/$limit auto-pagination (including the max_items cap
and the short-last-page stop). Also proves non-2xx statuses become teaching
NutanixApiError messages and that ConnectionManager reuses sessions.
"""

from __future__ import annotations

from typing import Any

import pytest

from nutanix_aiops.config import AppConfig, TargetConfig
from nutanix_aiops.connection import (
    ConnectionManager,
    NutanixApiError,
    NutanixConnection,
    _teaching_message,
)


class FakeResponse:
    def __init__(self, status_code=200, headers=None, json_body=None, text="", content=b"x"):
        self.status_code = status_code
        self.headers = headers or {}
        self._json = json_body if json_body is not None else {}
        self.text = text
        self.content = content

    def json(self) -> Any:
        return self._json


class FakeClient:
    """Records requests and replays a scripted callable or a fixed response."""

    def __init__(self, responder):
        self._responder = responder
        self.calls: list[dict] = []
        self.closed = False

    def request(self, method, path, **kwargs):
        self.calls.append({"method": method, "path": path, **kwargs})
        return self._responder(method, path, **kwargs)

    def close(self):
        self.closed = True


def _target() -> TargetConfig:
    # password is a resolved property (encrypted store), not a field; never read
    # here because a client is injected, so no secret store is needed.
    return TargetConfig(name="pc", host="pc.local", port=9440,
                        username="admin", verify_ssl=False)


def _conn(responder) -> tuple[NutanixConnection, FakeClient]:
    client = FakeClient(responder)
    return NutanixConnection(_target(), client=client), client


@pytest.mark.unit
def test_get_with_etag_reads_standard_and_vendor_headers():
    conn, _ = _conn(lambda *a, **k: FakeResponse(headers={"ETag": "v-7"},
                                                 json_body={"data": {"extId": "x"}}))
    body, etag = conn.get_with_etag("/api/thing/1")
    assert etag == "v-7"
    assert body["data"]["extId"] == "x"

    conn2, _ = _conn(lambda *a, **k: FakeResponse(headers={"X-Nutanix-Entity-Tag": "v-9"},
                                                  json_body={}))
    _, etag2 = conn2.get_with_etag("/api/thing/1")
    assert etag2 == "v-9"

    conn3, _ = _conn(lambda *a, **k: FakeResponse(headers={}, json_body={}))
    _, etag3 = conn3.get_with_etag("/api/thing/1")
    assert etag3 == ""  # no ETag → mutate proceeds without If-Match


@pytest.mark.unit
def test_mutate_sends_if_match_header_when_etag_present():
    conn, client = _conn(lambda *a, **k: FakeResponse(json_body={"ok": True}))
    conn.put("/api/thing/1", etag="v-3", json={"name": "n"})
    assert client.calls[-1]["headers"] == {"If-Match": "v-3"}

    conn.delete("/api/thing/1")  # no etag → no If-Match header
    assert client.calls[-1]["headers"] is None


@pytest.mark.unit
def test_list_all_walks_pages_and_sends_page_limit_params():
    # 50 rows on page 0 (full page → keep going), 3 rows on page 1 (short → stop).
    def responder(method, path, **kwargs):
        page = kwargs["params"]["$page"]
        rows = [{"i": i} for i in range(50 if page == 0 else 3)]
        return FakeResponse(json_body={"data": rows})

    conn, client = _conn(responder)
    rows = conn.list_all("/api/vmm/v4.0/ahv/config/vms")
    assert len(rows) == 53
    # page 0 then page 1, each carrying $limit=50
    assert [c["params"]["$page"] for c in client.calls] == [0, 1]
    assert client.calls[0]["params"]["$limit"] == 50


@pytest.mark.unit
def test_list_all_respects_max_items_cap():
    def responder(method, path, **kwargs):
        return FakeResponse(json_body={"data": [{"i": i} for i in range(10)]})

    conn, client = _conn(responder)
    rows = conn.list_all("/api/x", limit=10, max_items=25)
    assert len(rows) == 25  # capped, even though pages keep returning full batches
    # never pages past what the cap requires
    assert len(client.calls) == 3


@pytest.mark.unit
def test_list_all_merges_extra_params():
    def responder(method, path, **kwargs):
        return FakeResponse(json_body={"data": []})

    conn, client = _conn(responder)
    conn.list_all("/api/x", params={"$filter": "severity eq 'CRITICAL'"})
    assert client.calls[0]["params"]["$filter"] == "severity eq 'CRITICAL'"


@pytest.mark.unit
def test_non_2xx_raises_teaching_error_with_status_and_path():
    conn, _ = _conn(lambda *a, **k: FakeResponse(status_code=412, text="stale tag"))
    with pytest.raises(NutanixApiError) as ei:
        conn.get("/api/thing/1")
    assert ei.value.status_code == 412
    assert ei.value.path == "/api/thing/1"
    assert "If-Match" in str(ei.value)


@pytest.mark.unit
def test_transport_error_is_translated_to_reachability_message():
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("refused")

    conn, _ = _conn(boom)
    with pytest.raises(NutanixApiError, match="Could not reach Prism Central"):
        conn.get("/api/thing/1")


@pytest.mark.unit
def test_empty_and_invalid_body_json_degrade_to_empty_dict():
    conn, _ = _conn(lambda *a, **k: FakeResponse(content=b"", json_body={"x": 1}))
    assert conn.get("/api/thing/1") == {}  # no content → {}

    class BadJson(FakeResponse):
        def json(self):
            raise ValueError("not json")

    conn2, _ = _conn(lambda *a, **k: BadJson(content=b"garbage"))
    assert conn2.get("/api/thing/1") == {}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "needle"),
    [
        (401, "Authentication/authorization failed"),
        (404, "not found"),
        (409, "stale"),
        (422, "Validation error"),
        (503, "server error"),
        (418, "Prism Central API error"),
    ],
)
def test_teaching_message_branches(status, needle):
    assert needle.lower() in _teaching_message(status, "/p", "body").lower()


@pytest.mark.unit
def test_connection_manager_reuses_and_disconnects_sessions(monkeypatch):
    # ConnectionManager builds a REAL httpx-backed connection whose auth reads
    # target.password (the encrypted-store property); supply the legacy env
    # fallback so no secret store is needed for this session-reuse test.
    monkeypatch.setenv("NUTANIX_PC_PASSWORD", "pw")
    cfg = AppConfig(targets=(_target(),))
    mgr = ConnectionManager(cfg)
    c1 = mgr.connect("pc")
    c2 = mgr.connect("pc")
    assert c1 is c2  # cached
    assert mgr.list_targets() == ["pc"]
    assert mgr.list_connected() == ["pc"]
    mgr.disconnect_all()
    assert mgr.list_connected() == []
