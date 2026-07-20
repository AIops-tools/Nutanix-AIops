"""Extra ops-layer coverage: catalog / alerts RCA branches / dataprotection
writes / network / storage / lcm / fleet-overview.

No real Prism Central — the connection is a MagicMock throughout. Assertions are
real: endpoint paths + params, ETag plumbing, BEFORE-state capture, the
deterministic RCA heuristic branches, and the resilient overview roll-up.
"""

from unittest.mock import MagicMock

import pytest

# ── catalog ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_delete_image_captures_prior_name_and_sends_etag():
    from nutanix_aiops.ops import catalog as ops

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": {"extId": "img-1", "name": "ubuntu.iso"}},
                                       "etag-3")
    result = ops.delete_image(conn, "img-1")
    assert result["priorState"] == {"name": "ubuntu.iso"}
    conn.delete.assert_called_once_with("/api/vmm/v4.0/content/images/img-1", etag="etag-3")


@pytest.mark.unit
def test_delete_image_raises_keyerror_when_absent():
    from nutanix_aiops.ops import catalog as ops

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": {}}, "etag-0")
    with pytest.raises(KeyError, match="img-404"):
        ops.delete_image(conn, "img-404")
    conn.delete.assert_not_called()


@pytest.mark.unit
def test_create_category_surfaces_ext_id_when_synchronous():
    from nutanix_aiops.ops import catalog as ops

    conn = MagicMock(name="conn")
    conn.post.return_value = {"data": {"extId": "cat-9"}}
    result = ops.create_category(conn, "Environment", "Prod", description="prod tag")
    assert result["extId"] == "cat-9"
    assert "taskExtId" not in result
    conn.post.assert_called_once_with(
        "/api/prism/v4.0/config/categories",
        json={"key": "Environment", "value": "Prod", "description": "prod tag"},
    )


@pytest.mark.unit
def test_create_category_falls_back_to_task_ext_id():
    from nutanix_aiops.ops import catalog as ops

    conn = MagicMock(name="conn")
    conn.post.return_value = {"data": {}}  # no extId → async task shape
    result = ops.create_category(conn, "k", "v")
    assert "extId" not in result
    assert result["taskExtId"] == ""


@pytest.mark.unit
def test_list_categories_normalizes():
    from nutanix_aiops.ops import catalog as ops

    conn = MagicMock(name="conn")
    conn.list_all.return_value = [
        {"extId": "cat-1", "key": "Env", "value": "Prod", "description": "d"},
    ]
    rows = ops.list_categories(conn)["categories"]
    assert conn.list_all.call_args[0][0] == "/api/prism/v4.0/config/categories"
    assert rows == [{"extId": "cat-1", "key": "Env", "value": "Prod", "description": "d"}]


# ── alerts: resolve + RCA heuristic branches ───────────────────────────────


@pytest.mark.unit
def test_resolve_alert_captures_prior_resolved_state():
    from nutanix_aiops.ops import alerts as ops

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": {"extId": "a1", "resolved": False}}, "etag-2")
    result = ops.resolve_alert(conn, "a1")
    assert result["priorState"] == {"resolved": False}
    conn.post.assert_called_once_with(
        "/api/monitoring/v4.0/serviceability/alerts/a1/$actions/resolve",
        etag="etag-2", json={})


@pytest.mark.unit
def test_list_events_and_audits_normalize():
    from nutanix_aiops.ops import alerts as ops

    conn = MagicMock(name="conn")
    conn.list_all.return_value = [
        {"extId": "e1", "title": "evt", "creationTime": "t", "sourceEntityExtId": "s1"},
    ]
    events = ops.list_events(conn)["events"]
    assert conn.list_all.call_args[0][0] == "/api/monitoring/v4.0/serviceability/events"
    assert events == [{"extId": "e1", "title": "evt", "creationTime": "t",
                       "sourceEntityExtId": "s1"}]

    conn.list_all.return_value = [
        {"extId": "au1", "operationType": "UPDATE", "user": "root", "creationTime": "t"},
    ]
    audits = ops.list_audits(conn)["audits"]
    assert conn.list_all.call_args[0][0] == "/api/prism/v4.0/config/audits"
    assert audits == [{"extId": "au1", "operationType": "UPDATE", "user": "root",
                       "creationTime": "t"}]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("impact", "severity", "cause_needle", "action_needle"),
    [
        ("Performance", "WARNING", "performance", "CPU/memory/storage-latency"),
        ("Availability", "WARNING", "availability", "up and reachable"),
        ("Configuration", "WARNING", "configuration", "configuration changes"),
        ("", "CRITICAL", "critical alert", "Escalate"),
        ("", "INFO", "No specific impact", "related events below"),
    ],
)
def test_analyze_alert_probable_cause_and_action_branches(
    impact, severity, cause_needle, action_needle
):
    from nutanix_aiops.ops import alerts as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {"data": {
        "extId": "a1", "title": "t", "severity": severity,
        "impactType": impact, "affectedEntityExtId": ""}}
    conn.list_all.return_value = []  # no events; affected entity blank → none correlate

    out = ops.analyze_alert(conn, "a1")
    assert cause_needle.lower() in out["probableCause"].lower()
    assert any(action_needle.lower() in a.lower() for a in out["suggestedActions"])
    assert out["relatedEvents"] == []


# ── dataprotection writes ──────────────────────────────────────────────────


@pytest.mark.unit
def test_list_snapshots_and_protection_domains_normalize():
    from nutanix_aiops.ops import dataprotection as ops

    conn = MagicMock(name="conn")
    conn.list_all.return_value = [
        {"extId": "snap-1", "name": "nightly", "createTimeUsecs": 5, "vmExtId": "v1"},
    ]
    snaps = ops.list_snapshots(conn, "v1")["snapshots"]
    assert conn.list_all.call_args[0][0] == "/api/vmm/v4.0/ahv/config/vms/v1/snapshots"
    assert snaps == [{"extId": "snap-1", "name": "nightly", "createTimeUsecs": 5,
                      "vmExtId": "v1"}]

    conn.list_all.return_value = [{"extId": "pd-1", "name": "gold", "replicationType": "ASYNC"}]
    pds = ops.list_protection_domains(conn)["protectionDomains"]
    assert conn.list_all.call_args[0][0] == "/api/dataprotection/v4.0/config/protection-policies"
    assert pds == [{"extId": "pd-1", "name": "gold", "replicationType": "ASYNC"}]


@pytest.mark.unit
def test_delete_snapshot_captures_prior_name_and_sends_etag():
    from nutanix_aiops.ops import dataprotection as ops

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": {"extId": "snap-1", "name": "nightly"}},
                                       "etag-9")
    result = ops.delete_snapshot(conn, "v1", "snap-1")
    assert result["priorState"] == {"name": "nightly"}
    conn.delete.assert_called_once_with(
        "/api/vmm/v4.0/ahv/config/vms/v1/snapshots/snap-1", etag="etag-9")


@pytest.mark.unit
def test_restore_snapshot_captures_prior_power_and_posts_revert():
    from nutanix_aiops.ops import dataprotection as ops

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": {"extId": "v1", "powerState": "ON"}}, "etag-1")
    conn.post.return_value = {"data": {"extId": "task-1"}}
    result = ops.restore_snapshot(conn, "v1", "snap-1")
    assert result["priorState"] == {"powerState": "ON"}
    assert result["taskExtId"] == "task-1"
    args, kwargs = conn.post.call_args
    assert args[0] == "/api/vmm/v4.0/ahv/config/vms/v1/$actions/revert"
    assert kwargs["json"] == {"snapshotExtId": "snap-1"}


@pytest.mark.unit
def test_protect_vm_and_failover_pd_post_to_policy_actions():
    from nutanix_aiops.ops import dataprotection as ops

    conn = MagicMock(name="conn")
    conn.post.return_value = {}
    result = ops.protect_vm(conn, "v1", "pol-1")
    assert result == {"action": "protect_vm", "vmExtId": "v1", "policyExtId": "pol-1"}
    conn.post.assert_called_with(
        "/api/dataprotection/v4.0/config/protection-policies/pol-1/$actions/associate-vm",
        json={"vmExtId": "v1"})

    conn.post.return_value = {"data": {"extId": "task-7"}}
    fo = ops.failover_pd(conn, "pol-1", "cl-2")
    assert fo["taskExtId"] == "task-7"
    assert fo["targetClusterExtId"] == "cl-2"
    conn.post.assert_called_with(
        "/api/dataprotection/v4.0/config/protection-policies/pol-1/$actions/failover",
        json={"targetClusterExtId": "cl-2"})


# ── network + storage writes ───────────────────────────────────────────────


@pytest.mark.unit
def test_create_subnet_builds_spec():
    from nutanix_aiops.ops import network as ops

    conn = MagicMock(name="conn")
    conn.post.return_value = {"data": {"extId": "task-1"}}
    result = ops.create_subnet(conn, "vlan200", "cl-1", 200)
    assert result["taskExtId"] == "task-1"
    args, kwargs = conn.post.call_args
    assert args[0] == "/api/networking/v4.0/config/subnets"
    assert kwargs["json"]["networkId"] == 200
    assert kwargs["json"]["clusterReference"] == {"extId": "cl-1"}


@pytest.mark.unit
def test_delete_subnet_captures_prior_state_and_sends_the_etag():
    """priorState is the whole subnet, not just its name — see test_network.py
    for the field-by-field assertions; this pins the ETag round trip."""
    from nutanix_aiops.ops import network as ops

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": {"extId": "sn1", "name": "vlan100"}}, "etag-4")
    result = ops.delete_subnet(conn, "sn1")
    assert result["priorState"]["name"] == "vlan100"
    conn.delete.assert_called_once_with(
        "/api/networking/v4.0/config/subnets/sn1", etag="etag-4")


@pytest.mark.unit
def test_get_subnet_raises_keyerror_when_absent():
    from nutanix_aiops.ops import network as ops

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": {}}, "etag-0")
    with pytest.raises(KeyError, match="sn-404"):
        ops.get_subnet(conn, "sn-404")


@pytest.mark.unit
def test_norm_subnet_falls_back_to_ipv6_block():
    from nutanix_aiops.ops import network as ops

    conn = MagicMock(name="conn")
    conn.list_all.return_value = [{
        "extId": "sn6", "name": "v6net", "subnetType": "VLAN",
        "ipConfig": [{"ipv6": {"ipSubnet": {"ip": {"value": "fd00::"}, "prefixLength": 64}}}],
    }]
    (row,) = ops.list_subnets(conn)["subnets"]
    assert row["ipConfig"]["cidr"] == "fd00::/64"


@pytest.mark.unit
def test_create_and_delete_storage_container():
    from nutanix_aiops.ops import storage as ops

    conn = MagicMock(name="conn")
    conn.post.return_value = {"data": {"extId": "task-1"}}
    created = ops.create_storage_container(conn, "sc-new", "cl-1", replication_factor=3)
    assert created["taskExtId"] == "task-1"
    _, kwargs = conn.post.call_args
    assert kwargs["json"]["replicationFactor"] == 3

    conn.get_with_etag.return_value = (
        {"data": {"extId": "sc1", "name": "old", "maxCapacityBytes": 10, "replicationFactor": 2}},
        "etag-8",
    )
    deleted = ops.delete_storage_container(conn, "sc1")
    assert deleted["priorState"] == {"maxCapacityBytes": 10, "replicationFactor": 2}
    conn.delete.assert_called_once_with(
        "/api/clustermgmt/v4.0/config/storage-containers/sc1", etag="etag-8")


@pytest.mark.unit
def test_container_raw_raises_keyerror_when_absent():
    from nutanix_aiops.ops import storage as ops

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": {}}, "etag-0")
    with pytest.raises(KeyError, match="sc-404"):
        ops.delete_storage_container(conn, "sc-404")


# ── lcm precheck ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_run_precheck_posts_cluster_scoped_specs():
    from nutanix_aiops.ops import lcm as ops

    conn = MagicMock(name="conn")
    conn.post.return_value = {"data": {"extId": "task-1"}}
    result = ops.run_precheck(conn, "cl-1", ["e1", "e2"])
    assert result["action"] == "lcm_precheck"
    assert result["entityCount"] == 2
    args, kwargs = conn.post.call_args
    assert args[0] == "/api/lifecycle/v4.0/resources/$actions/perform-precheck"
    assert kwargs["json"] == {
        "clusterExtId": "cl-1",
        "entityUpdateSpecs": [{"entityExtId": "e1"}, {"entityExtId": "e2"}],
    }


# ── fleet overview ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_fleet_overview_rolls_up_counts_and_spreads():
    from nutanix_aiops.ops import overview as ops

    conn = MagicMock(name="conn")

    def _list_all(path, **_kw):
        if path.endswith("/clusters"):
            return [{"extId": "cl-1", "name": "c"}]
        if path.endswith("/hosts"):
            return [{"extId": "h-1"}, {"extId": "h-2"}]
        if path.endswith("/vms"):
            return [
                {"extId": "v1", "powerState": "ON", "hypervisorType": "AHV"},
                {"extId": "v2", "powerState": "OFF", "hypervisorType": "AHV"},
                {"extId": "v3", "powerState": "ON", "source": {"entityType": "ESXi"}},
            ]
        return []

    conn.list_all.side_effect = _list_all
    out = ops.fleet_overview(conn)
    assert out["clusters"] == 1
    assert out["hosts"] == 2
    assert out["vms"] == 3
    assert out["hypervisorSpread"] == {"AHV": 2, "ESXi": 1}
    assert out["powerStateSpread"]["ON"] == 2
    assert out["errors"] == []


@pytest.mark.unit
def test_fleet_overview_is_resilient_and_collects_errors():
    from nutanix_aiops.ops import overview as ops

    conn = MagicMock(name="conn")
    conn.list_all.side_effect = RuntimeError("prism down")
    out = ops.fleet_overview(conn)
    assert out["clusters"] == 0 and out["hosts"] == 0 and out["vms"] == 0
    assert len(out["errors"]) == 3  # clusters, hosts, vms each degraded
    assert all("prism down" in e for e in out["errors"])
