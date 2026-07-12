"""Unit tests for the tasks + capacity-runway module (ops + MCP tools).

No real Prism Central: the connection is a MagicMock. Proves task_list
normalises and applies the status $filter, that capacity_runway is a
deterministic forecast (days-to-full with a given growth rate, insufficient
data without one), and that both MCP tools are read-only (risk=low) and carry
the governance marker.
"""

from unittest.mock import MagicMock

import pytest


@pytest.mark.unit
def test_task_list_normalizes_and_applies_status_filter():
    from nutanix_aiops.ops import capacity as ops

    conn = MagicMock(name="conn")
    conn.list_all.return_value = [{
        "extId": "task-1", "operation": "VmCreate", "status": "RUNNING",
        "percentageComplete": 42, "createdTime": "2026-07-12T00:00:00Z",
        "entitiesAffected": [{"extId": "vm-9"}],
    }]
    rows = ops.list_tasks(conn, status="RUNNING")
    conn.list_all.assert_called_once_with(
        "/api/prism/v4.0/config/tasks", params={"$filter": "status eq 'RUNNING'"}
    )
    assert rows == [{
        "extId": "task-1", "operation": "VmCreate", "status": "RUNNING",
        "percentageComplete": 42, "createdTime": "2026-07-12T00:00:00Z",
        "entityExtId": "vm-9",
    }]


@pytest.mark.unit
def test_capacity_runway_computes_days_to_full():
    from nutanix_aiops.ops import capacity as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {"data": {
        "extId": "cl-1",
        "stats": {"storageUsageBytes": 600, "storageCapacityBytes": 1000},
    }}
    result = ops.get_capacity_runway(conn, "cl-1", daily_growth_bytes=100)
    assert result["clusterExtId"] == "cl-1"
    assert result["freeBytes"] == 400
    assert result["usedPercent"] == 60.0
    assert result["daysToFull"] == 4
    assert result["forecast"] == "ok"


@pytest.mark.unit
def test_capacity_runway_insufficient_data_without_growth():
    from nutanix_aiops.ops import capacity as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {"data": {
        "extId": "cl-1",
        "stats": {"storageUsageBytes": 600, "storageCapacityBytes": 1000},
    }}
    result = ops.get_capacity_runway(conn, "cl-1")
    assert result["daysToFull"] is None
    assert result["forecast"] == "insufficient-data"
    assert result["freeBytes"] == 400


@pytest.mark.unit
def test_both_tools_are_read_only_and_governed():
    from mcp_server.tools import capacity

    assert capacity.task_list._risk_level == "low"
    assert capacity.capacity_runway._risk_level == "low"
    assert capacity.task_list._is_governed_tool is True
    assert capacity.capacity_runway._is_governed_tool is True
