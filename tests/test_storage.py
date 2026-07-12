"""Unit tests for the storage-container domain module.

Proves: list normalisation, that update captures BEFORE-state and sends the
If-Match ETag, that write-tool risk tiers are correct, and that a dry-run delete
never mutates. No real Prism Central — the connection is a MagicMock.
"""

from unittest.mock import MagicMock

import pytest


@pytest.mark.unit
def test_list_storage_containers_normalizes_row():
    from nutanix_aiops.ops import storage as ops

    conn = MagicMock(name="conn")
    conn.list_all.return_value = [{
        "extId": "sc1", "name": "default-container",
        "clusterExtId": "cl1", "maxCapacityBytes": 1024,
        "logicalUsageBytes": 512, "replicationFactor": 2,
    }]
    rows = ops.list_storage_containers(conn)
    assert rows == [{
        "extId": "sc1", "name": "default-container",
        "clusterExtId": "cl1", "maxCapacityBytes": 1024,
        "logicalUsageBytes": 512, "replicationFactor": 2,
    }]


@pytest.mark.unit
def test_update_captures_prior_state_and_sends_etag():
    from nutanix_aiops.ops import storage as ops

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = (
        {"data": {"extId": "sc1", "name": "default-container",
                  "maxCapacityBytes": 1024, "replicationFactor": 2}},
        "etag-7",
    )
    conn.put.return_value = {}
    result = ops.update_storage_container(conn, "sc1", max_capacity_bytes=2048)
    assert result["action"] == "update_storage_container"
    assert result["priorState"] == {"maxCapacityBytes": 1024, "replicationFactor": 2}
    args, kwargs = conn.put.call_args
    assert args[0] == "/api/clustermgmt/v4.0/config/storage-containers/sc1"
    assert kwargs["etag"] == "etag-7"
    assert kwargs["json"]["maxCapacityBytes"] == 2048


@pytest.mark.unit
def test_write_tools_have_correct_risk_tiers():
    from mcp_server.tools import storage

    assert storage.storage_container_create._risk_level == "medium"
    assert storage.storage_container_update._risk_level == "medium"
    assert storage.storage_container_delete._risk_level == "high"


@pytest.mark.unit
def test_mcp_delete_dry_run_does_not_mutate(monkeypatch):
    from mcp_server.tools import storage

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = (
        {"data": {"extId": "sc1", "name": "default-container",
                  "maxCapacityBytes": 1024, "replicationFactor": 2}},
        "etag-7",
    )
    monkeypatch.setattr(storage, "_get_connection", lambda target=None: conn)

    result = storage.storage_container_delete(ext_id="sc1", dry_run=True)
    assert result["dryRun"] is True
    assert result["wouldDelete"]["name"] == "default-container"
    conn.delete.assert_not_called()
