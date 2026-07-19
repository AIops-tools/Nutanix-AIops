"""Tests for the diagnostics / RCA layer.

The heuristics in ``nutanix_aiops.ops.diagnostics`` are pure functions, so every
threshold is exercised directly against synthetic telemetry — no Prism Central,
no connection, no clock. Two MCP-level tests then prove the governed wrappers
collect the right inventory and carry the harness marker.
"""

from unittest.mock import MagicMock

import pytest

from nutanix_aiops.ops import diagnostics as diag

# ── fixtures: a healthy baseline each test mutates a copy of ────────────────

_HEALTHY_CLUSTER = {
    "extId": "c1",
    "name": "prod-cluster",
    "nodeCount": 2,
    "resiliencyState": "NORMAL",
    "upgradeStatus": None,
    "storageUsageBytes": 100,
    "storageCapacityBytes": 1000,
}
_HEALTHY_HOSTS = [
    {"extId": "h1", "name": "node-a", "clusterExtId": "c1", "nodeStatus": "NORMAL"},
    {"extId": "h2", "name": "node-b", "clusterExtId": "c1", "nodeStatus": "NORMAL"},
]
_HEALTHY_CONTAINERS = [
    {"extId": "sc1", "name": "default-ctr", "logicalUsageBytes": 10, "maxCapacityBytes": 1000},
]


def _signals(result: dict) -> list[str]:
    return [f["signal"] for f in result["findings"]]


# ── cluster_health_findings ────────────────────────────────────────────────


@pytest.mark.unit
def test_healthy_estate_yields_no_findings():
    result = diag.cluster_health_findings(
        [dict(_HEALTHY_CLUSTER)], list(_HEALTHY_HOSTS), list(_HEALTHY_CONTAINERS)
    )
    assert result["findings"] == []
    assert result["clustersAnalyzed"] == 1
    assert result["hostsAnalyzed"] == 2
    assert result["containersAnalyzed"] == 1


@pytest.mark.unit
def test_degraded_resiliency_is_critical_and_cites_the_state():
    cluster = {**_HEALTHY_CLUSTER, "resiliencyState": "CRITICAL"}
    result = diag.cluster_health_findings([cluster], list(_HEALTHY_HOSTS), [])
    found = next(f for f in result["findings"] if f["signal"] == "cluster resiliency degraded")
    assert found["severity"] == "critical"
    assert "CRITICAL" in found["detail"]
    assert found["resource"] == "prod-cluster"


@pytest.mark.unit
def test_pool_usage_warn_and_critical_thresholds():
    warn = diag.cluster_health_findings(
        [{**_HEALTHY_CLUSTER, "storageUsageBytes": 850, "storageCapacityBytes": 1000}], [], []
    )["findings"][0]
    assert warn["severity"] == "warning"
    assert "85.0%" in warn["detail"] and "80.0%" in warn["detail"]

    crit = diag.cluster_health_findings(
        [{**_HEALTHY_CLUSTER, "storageUsageBytes": 950, "storageCapacityBytes": 1000}], [], []
    )["findings"][0]
    assert crit["severity"] == "critical"
    assert "95.0%" in crit["detail"]


@pytest.mark.unit
def test_just_under_threshold_is_clean():
    result = diag.cluster_health_findings(
        [{**_HEALTHY_CLUSTER, "storageUsageBytes": 799, "storageCapacityBytes": 1000}], [], []
    )
    assert result["findings"] == []


@pytest.mark.unit
def test_container_over_threshold_is_flagged_with_its_name():
    containers = [
        {"extId": "sc2", "name": "hot-ctr", "logicalUsageBytes": 910, "maxCapacityBytes": 1000}
    ]
    result = diag.cluster_health_findings([], [], containers)
    found = result["findings"][0]
    assert found["severity"] == "critical"
    assert found["resource"] == "hot-ctr"
    assert found["signal"] == "storage container near full"


@pytest.mark.unit
def test_unhealthy_node_status_is_critical():
    hosts = [_HEALTHY_HOSTS[0], {**_HEALTHY_HOSTS[1], "nodeStatus": "DEGRADED"}]
    result = diag.cluster_health_findings([dict(_HEALTHY_CLUSTER)], hosts, [])
    found = next(f for f in result["findings"] if f["signal"] == "node not healthy")
    assert found["severity"] == "critical"
    assert found["resource"] == "node-b"
    assert "DEGRADED" in found["detail"]


@pytest.mark.unit
def test_missing_host_versus_node_count_is_flagged():
    result = diag.cluster_health_findings([dict(_HEALTHY_CLUSTER)], [_HEALTHY_HOSTS[0]], [])
    found = next(f for f in result["findings"] if f["signal"] == "node missing from inventory")
    assert "nodeCount=2" in found["detail"]


@pytest.mark.unit
def test_cluster_probe_error_is_reported_not_swallowed():
    result = diag.cluster_health_findings([{"name": "broken", "error": "connect timeout"}], [], [])
    found = result["findings"][0]
    assert found["signal"] == "health probe failed"
    assert "connect timeout" in found["detail"]


@pytest.mark.unit
def test_upgrade_in_progress_is_info_only():
    cluster = {**_HEALTHY_CLUSTER, "upgradeStatus": "UPGRADING"}
    result = diag.cluster_health_findings([cluster], list(_HEALTHY_HOSTS), [])
    assert _signals(result) == ["upgrade in progress"]
    assert result["findings"][0]["severity"] == "info"


@pytest.mark.unit
def test_cluster_findings_are_ranked_worst_first():
    cluster = {
        **_HEALTHY_CLUSTER,
        "resiliencyState": "CRITICAL",
        "upgradeStatus": "UPGRADING",
        "storageUsageBytes": 850,
        "storageCapacityBytes": 1000,
    }
    severities = [
        f["severity"] for f in diag.cluster_health_findings([cluster], [], [])["findings"]
    ]
    assert severities == ["critical", "warning", "info"]


@pytest.mark.unit
def test_cluster_analysis_survives_missing_fields():
    result = diag.cluster_health_findings([{}], [{}], [{}])
    assert result["findings"] == []  # nothing measurable → nothing invented
    assert result["summary"][0]["storagePoolPct"] is None


@pytest.mark.unit
def test_cluster_analysis_does_not_mutate_its_inputs():
    cluster = {**_HEALTHY_CLUSTER, "resiliencyState": "CRITICAL"}
    before = dict(cluster)
    diag.cluster_health_findings([cluster], list(_HEALTHY_HOSTS), list(_HEALTHY_CONTAINERS))
    assert cluster == before


# ── alert_triage_findings ──────────────────────────────────────────────────


def _alert(ext_id, severity, created, *, resolved=False, acknowledged=True, title="t"):
    return {
        "extId": ext_id,
        "title": title,
        "severity": severity,
        "creationTime": created,
        "resolved": resolved,
        "acknowledged": acknowledged,
    }


@pytest.mark.unit
def test_no_active_alerts_yields_no_findings():
    rows = [_alert("a1", "CRITICAL", "2026-07-01T00:00:00Z", resolved=True)]
    result = diag.alert_triage_findings(rows)
    assert result["findings"] == []
    assert result["activeAlerts"] == 0
    assert result["alertsAnalyzed"] == 1


@pytest.mark.unit
def test_severity_counts_and_worst_first_ordering():
    rows = [
        _alert("a1", "INFO", "2026-07-10T00:00:00Z"),
        _alert("a2", "CRITICAL", "2026-07-10T00:00:00Z"),
        _alert("a3", "WARNING", "2026-07-10T00:00:00Z"),
        _alert("a4", "CRITICAL", "2026-07-10T00:00:00Z"),
    ]
    result = diag.alert_triage_findings(rows)
    assert result["severityCounts"] == {"INFO": 1, "CRITICAL": 2, "WARNING": 1}
    severities = [f["severity"] for f in result["findings"]]
    assert severities == sorted(severities, key=lambda s: {"critical": 0, "warning": 1}.get(s, 2))
    crit = next(f for f in result["findings"] if "CRITICAL" in f["signal"])
    assert "2 active CRITICAL alert(s)" == crit["signal"]


@pytest.mark.unit
def test_unacknowledged_criticals_are_called_out():
    rows = [_alert("a1", "CRITICAL", "2026-07-10T00:00:00Z", acknowledged=False)]
    result = diag.alert_triage_findings(rows)
    found = next(f for f in result["findings"] if f["signal"] == "unacknowledged critical alerts")
    assert "1 critical alert(s) not yet acknowledged" in found["detail"]


@pytest.mark.unit
def test_oldest_unresolved_is_surfaced_with_age_relative_to_newest():
    rows = [
        _alert("old", "WARNING", "2026-07-01T00:00:00Z", title="disk latency"),
        _alert("new", "INFO", "2026-07-11T00:00:00Z"),
    ]
    result = diag.alert_triage_findings(rows)
    assert result["oldestUnresolved"]["extId"] == "old"
    assert result["oldestUnresolved"]["ageDays"] == 10.0
    stale = next(f for f in result["findings"] if f["signal"] == "stale unresolved alert")
    assert stale["severity"] == "warning"
    assert "10.0 day(s)" in stale["detail"]
    assert stale["resource"] == "disk latency"


@pytest.mark.unit
def test_fresh_alerts_do_not_trip_the_stale_threshold():
    rows = [
        _alert("a1", "WARNING", "2026-07-10T00:00:00Z"),
        _alert("a2", "WARNING", "2026-07-12T00:00:00Z"),
    ]
    result = diag.alert_triage_findings(rows)
    assert "stale unresolved alert" not in _signals(result)
    assert result["oldestUnresolved"]["ageDays"] == 2.0


@pytest.mark.unit
def test_explicit_now_iso_overrides_the_feed_reference():
    rows = [_alert("a1", "WARNING", "2026-07-01T00:00:00Z")]
    result = diag.alert_triage_findings(rows, now_iso="2026-07-20T00:00:00Z")
    assert result["oldestUnresolved"]["ageDays"] == 19.0
    assert "stale unresolved alert" in _signals(result)


@pytest.mark.unit
def test_alert_triage_survives_missing_and_unparseable_fields():
    rows = [{}, {"severity": "CRITICAL"}, {"creationTime": "not-a-date"}]
    result = diag.alert_triage_findings(rows)
    assert result["activeAlerts"] == 3
    assert result["oldestUnresolved"] is None  # no parseable timestamp → no guess
    assert result["severityCounts"]["UNKNOWN"] == 2


@pytest.mark.unit
def test_alert_triage_does_not_mutate_its_inputs():
    rows = [_alert("a1", "CRITICAL", "2026-07-10T00:00:00Z", acknowledged=False)]
    before = [dict(r) for r in rows]
    diag.alert_triage_findings(rows)
    assert rows == before


# ── MCP layer: governed marker + correct collection ────────────────────────


@pytest.mark.unit
def test_mcp_cluster_health_rca_is_governed_and_collects_estate(monkeypatch):
    from mcp_server.tools import diagnostics as d

    assert d.cluster_health_rca._is_governed_tool is True
    assert d.cluster_health_rca._risk_level == "low"

    conn = MagicMock(name="conn")
    conn.list_all.side_effect = lambda path, **k: {
        "/api/clustermgmt/v4.0/config/clusters": [{"extId": "c1", "name": "prod"}],
        "/api/clustermgmt/v4.0/config/hosts": [
            {"extId": "h1", "hostName": "node-a", "clusterExtId": "c1", "nodeStatus": "DEGRADED"}
        ],
        "/api/clustermgmt/v4.0/config/storage-containers": [
            {"extId": "sc1", "name": "ctr", "logicalUsageBytes": 950, "maxCapacityBytes": 1000}
        ],
    }[path]
    conn.get.return_value = {
        "data": {
            "extId": "c1",
            "name": "prod",
            "config": {"faultToleranceState": "NORMAL"},
            "nodes": {"numberOfNodes": 1},
            "stats": {"storageUsageBytes": 10, "storageCapacityBytes": 1000},
        }
    }
    monkeypatch.setattr(d, "_get_connection", lambda target=None: conn)

    result = d.cluster_health_rca()
    assert "error" not in result
    assert result["clustersAnalyzed"] == 1
    assert result["containersAnalyzed"] == 1
    signals = {f["signal"] for f in result["findings"]}
    assert "node not healthy" in signals
    assert "storage container near full" in signals


@pytest.mark.unit
def test_mcp_alert_triage_rca_is_governed_and_collects_alerts(monkeypatch):
    from mcp_server.tools import diagnostics as d

    assert d.alert_triage_rca._is_governed_tool is True
    assert d.alert_triage_rca._risk_level == "low"

    conn = MagicMock(name="conn")
    conn.list_all.return_value = [
        {
            "extId": "a1",
            "title": "CVM down",
            "severity": "CRITICAL",
            "creationTime": "2026-07-01T00:00:00Z",
            "acknowledged": False,
            "resolved": False,
        },
        {
            "extId": "a2",
            "title": "old news",
            "severity": "INFO",
            "creationTime": "2026-07-12T00:00:00Z",
            "acknowledged": True,
            "resolved": True,
        },
    ]
    monkeypatch.setattr(d, "_get_connection", lambda target=None: conn)

    result = d.alert_triage_rca()
    assert "error" not in result
    assert result["alertsAnalyzed"] == 2
    assert result["activeAlerts"] == 1
    assert result["severityCounts"] == {"CRITICAL": 1}
    assert result["oldestUnresolved"]["extId"] == "a1"


@pytest.mark.unit
def test_rank_assigns_explicit_worst_first_rank():
    """Findings state their priority explicitly, not implicitly by list order.

    A consumer — notably a smaller local model summarising the result — must not
    have to infer urgency from a finding's position in the list.
    """
    from nutanix_aiops.ops import diagnostics as _diag

    ranked = _diag._rank([{"severity": "info"}, {"severity": "critical"}, {"severity": "warning"}])
    assert [f["severity"] for f in ranked] == ["critical", "warning", "info"]
    assert [f["rank"] for f in ranked] == [1, 2, 3]
