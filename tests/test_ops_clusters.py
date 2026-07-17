"""Unit tests for the cluster / host / utilization inventory ops module.

No real Prism Central — the connection is a MagicMock. Proves list_clusters and
list_hosts fold raw clustermgmt v4 payloads into the stable shape, that the
health / utilization single-entity reads hit the right per-cluster endpoint and
read the right nested fields, and that both single-entity reads degrade to an
``error`` field (never a raised traceback) when the fetch fails.
"""

from unittest.mock import MagicMock

import pytest

from nutanix_aiops.ops import clusters as ops

_CLUSTERS = "/api/clustermgmt/v4.0/config/clusters"
_HOSTS = "/api/clustermgmt/v4.0/config/hosts"


@pytest.mark.unit
def test_list_clusters_normalizes_nested_config_and_nodes():
    conn = MagicMock(name="conn")
    conn.list_all.return_value = [
        {
            "extId": "cl-1",
            "name": "prod-cluster",
            "config": {
                "buildInfo": {"version": "6.8"},
                "hypervisorTypes": ["AHV"],
                "clusterFunction": ["AOS"],
                "faultToleranceState": "OK",
            },
            "nodes": {"numberOfNodes": 4},
        }
    ]
    rows = ops.list_clusters(conn)
    conn.list_all.assert_called_once_with(_CLUSTERS)
    assert rows == [
        {
            "extId": "cl-1",
            "name": "prod-cluster",
            "aosVersion": "6.8",
            "hypervisorTypes": ["AHV"],
            "nodeCount": 4,
            "clusterFunction": ["AOS"],
        }
    ]


@pytest.mark.unit
def test_list_clusters_tolerates_missing_config_blocks():
    conn = MagicMock(name="conn")
    conn.list_all.return_value = [{"extId": "cl-2", "name": "bare"}]
    (row,) = ops.list_clusters(conn)
    assert row["aosVersion"] == ""
    assert row["hypervisorTypes"] == []
    assert row["nodeCount"] is None
    assert row["clusterFunction"] == []


@pytest.mark.unit
def test_get_cluster_health_reads_single_entity_and_folds_resiliency():
    conn = MagicMock(name="conn")
    conn.get.return_value = {
        "data": {
            "extId": "cl-1",
            "name": "prod-cluster",
            "config": {"aosVersion": "6.8", "faultToleranceState": "kNodeFault"},
            "upgradeStatus": "PENDING",
        }
    }
    result = ops.get_cluster_health(conn, "cl-1")
    conn.get.assert_called_once_with(f"{_CLUSTERS}/cl-1")
    assert result["extId"] == "cl-1"
    assert result["upgradeStatus"] == "PENDING"
    assert result["resiliencyState"] == "kNodeFault"


@pytest.mark.unit
def test_get_cluster_health_is_resilient_to_a_failing_fetch():
    conn = MagicMock(name="conn")
    conn.get.side_effect = RuntimeError("prism down")
    result = ops.get_cluster_health(conn, "cl-9")
    assert "error" in result
    assert result["clusterExtId"] == "cl-9"
    # a health probe must survive the thing it probes — no key beyond error/id
    assert "resiliencyState" not in result


@pytest.mark.unit
def test_list_hosts_normalizes_nested_references():
    conn = MagicMock(name="conn")
    conn.list_all.return_value = [
        {
            "extId": "h-1",
            "hostName": "node-a",
            "cluster": {"uuid": "cl-1"},
            "hypervisor": {"type": "AHV"},
            "numberOfCpuCores": 32,
            "memorySizeBytes": 137438953472,
            "bootTimeUsecs": 111,
        }
    ]
    rows = ops.list_hosts(conn)
    conn.list_all.assert_called_once_with(_HOSTS)
    assert rows == [
        {
            "extId": "h-1",
            "name": "node-a",
            "clusterExtId": "cl-1",
            "hypervisor": "AHV",
            "numCpuCores": 32,
            "memoryBytes": 137438953472,
            "bootTimeUsecs": 111,
        }
    ]


@pytest.mark.unit
def test_get_cluster_utilization_reads_stats_block():
    conn = MagicMock(name="conn")
    conn.get.return_value = {
        "data": {
            "extId": "cl-1",
            "name": "prod-cluster",
            "stats": {
                "hypervisorCpuUsagePpm": 250000,
                "hypervisorMemoryUsagePpm": 400000,
                "storageUsageBytes": 600,
                "storageCapacityBytes": 1000,
                "controllerNumIops": 1500,
            },
        }
    }
    result = ops.get_cluster_utilization(conn, "cl-1")
    conn.get.assert_called_once_with(f"{_CLUSTERS}/cl-1")
    assert result["cpuUsagePercent"] == 250000
    assert result["storageUsageBytes"] == 600
    assert result["iops"] == 1500


@pytest.mark.unit
def test_get_cluster_utilization_absent_stats_come_back_none_not_invented():
    conn = MagicMock(name="conn")
    conn.get.return_value = {"data": {"extId": "cl-1", "name": "prod"}}
    result = ops.get_cluster_utilization(conn, "cl-1")
    assert result["cpuUsagePercent"] is None
    assert result["storageCapacityBytes"] is None
    assert result["iops"] is None


@pytest.mark.unit
def test_get_cluster_utilization_is_resilient_to_a_failing_fetch():
    conn = MagicMock(name="conn")
    conn.get.side_effect = RuntimeError("boom")
    result = ops.get_cluster_utilization(conn, "cl-7")
    assert "error" in result
    assert result["clusterExtId"] == "cl-7"
