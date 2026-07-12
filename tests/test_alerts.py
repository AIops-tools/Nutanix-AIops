"""Unit tests for the alerts / events / audits + RCA domain module.

Proves: list_alerts normalises and applies the severity filter param, the two
writes capture BEFORE-state and send If-Match, the MCP risk tiers are correct,
and analyze_alert emits a probable-cause / suggested-actions heuristic while
staying resilient to a failing fetch. No real Prism Central — the connection is
a MagicMock.
"""

from unittest.mock import MagicMock

import pytest

from nutanix_aiops.ops import alerts as ops


@pytest.mark.unit
def test_list_alerts_normalizes_and_applies_severity_filter():
    conn = MagicMock(name="conn")
    conn.list_all.return_value = [
        {"extId": "a1", "title": "Disk failing", "severity": "CRITICAL",
         "impactType": "Availability", "creationTime": "t0", "acknowledged": False,
         "resolved": False, "affectedEntityExtId": "host-9"},
    ]
    rows = ops.list_alerts(conn, severity="CRITICAL")

    assert rows[0]["extId"] == "a1"
    assert rows[0]["severity"] == "CRITICAL"
    assert rows[0]["affectedEntityExtId"] == "host-9"
    assert rows[0]["acknowledged"] is False
    # severity filter must reach list_all as a $filter param
    _, kwargs = conn.list_all.call_args
    assert kwargs["params"] == {"$filter": "severity eq 'CRITICAL'"}


@pytest.mark.unit
def test_acknowledge_alert_captures_prior_state_and_posts_with_etag():
    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": {"extId": "a1", "acknowledged": False}},
                                       "etag-7")
    conn.post.return_value = {}

    result = ops.acknowledge_alert(conn, "a1")

    assert result["action"] == "acknowledge_alert"
    assert result["extId"] == "a1"
    assert result["priorState"]["acknowledged"] is False
    conn.post.assert_called_once_with(
        "/api/monitoring/v4.0/serviceability/alerts/a1/$actions/acknowledge",
        etag="etag-7", json={})


@pytest.mark.unit
def test_write_tools_have_correct_risk_tiers():
    from mcp_server.tools import alerts as a

    assert a.alert_acknowledge._risk_level == "low"
    assert a.alert_resolve._risk_level == "low"
    assert a.alert_list._risk_level == "low"
    assert a.analyze_alert._risk_level == "low"


@pytest.mark.unit
def test_analyze_alert_summarizes_and_is_resilient():
    conn = MagicMock(name="conn")
    conn.get.return_value = {"data": {
        "extId": "a1", "title": "Container 92% full", "severity": "WARNING",
        "impactType": "Capacity", "affectedEntityExtId": "ctr-3"}}
    conn.list_all.return_value = [
        {"extId": "e1", "title": "space low", "creationTime": "t1",
         "sourceEntityExtId": "ctr-3"},
        {"extId": "e2", "title": "unrelated", "creationTime": "t2",
         "sourceEntityExtId": "other"},
    ]

    out = ops.analyze_alert(conn, "a1")
    assert out["alert"]["affectedEntityExtId"] == "ctr-3"
    assert "capacity" in out["probableCause"].lower()
    assert out["suggestedActions"]  # non-empty heuristic
    # only same-entity events correlate
    assert [e["extId"] for e in out["relatedEvents"]] == ["e1"]

    # resilient: a failing fetch yields an error dict, never a raised traceback
    conn.get.side_effect = RuntimeError("boom")
    err = ops.analyze_alert(conn, "a1")
    assert "error" in err
    assert err["alertExtId"] == "a1"
