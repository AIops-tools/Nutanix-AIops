"""Unit tests for the dataprotection domain (snapshots / recovery points / PD).

No real Prism Central is needed — the connection is a MagicMock. Covers read
normalisation, ETag-aware snapshot create, write-tool risk tiers, and dry-run
gating on the destructive snapshot delete.
"""

from unittest.mock import MagicMock

import pytest


@pytest.mark.unit
def test_list_recovery_points_normalizes():
    from nutanix_aiops.ops import dataprotection as ops

    conn = MagicMock(name="conn")
    conn.list_all.return_value = [
        {"extId": "rp1", "vmExtId": "v1", "createTimeUsecs": 111,
         "expirationTimeUsecs": 222, "locationType": "LOCAL"},
    ]
    rows = ops.list_recovery_points(conn)["recoveryPoints"]
    assert rows == [
        {"extId": "rp1", "vmExtId": "v1", "createTimeUsecs": 111,
         "expirationTimeUsecs": 222, "locationType": "LOCAL"},
    ]
    assert conn.list_all.call_args[0][0] == "/api/dataprotection/v4.0/config/recovery-points"


@pytest.mark.unit
def test_create_snapshot_posts_to_vm_snapshots_path_with_etag():
    from nutanix_aiops.ops import dataprotection as ops

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": {"extId": "v1"}}, "etag-7")
    conn.post.return_value = {"data": {"extId": "task1"}}

    result = ops.create_snapshot(conn, "v1", "nightly")
    assert result["action"] == "create_snapshot"
    assert result["vmExtId"] == "v1"
    assert result["taskExtId"] == "task1"
    conn.get_with_etag.assert_called_once_with("/api/vmm/v4.0/ahv/config/vms/v1")
    conn.post.assert_called_once_with(
        "/api/vmm/v4.0/ahv/config/vms/v1/snapshots", etag="etag-7", json={"name": "nightly"}
    )


@pytest.mark.unit
def test_write_tools_have_correct_risk_tiers():
    from mcp_server.tools import dataprotection as d

    assert d.snapshot_create._risk_level == "medium"
    assert d.vm_protect._risk_level == "medium"
    assert d.snapshot_delete._risk_level == "high"
    assert d.snapshot_restore._risk_level == "high"
    assert d.pd_failover._risk_level == "high"


@pytest.mark.unit
def test_mcp_snapshot_delete_dry_run_does_not_mutate(monkeypatch):
    from mcp_server.tools import dataprotection as d

    conn = MagicMock(name="conn")
    monkeypatch.setattr(d, "_get_connection", lambda target=None: conn)

    result = d.snapshot_delete(vm_ext_id="v1", snapshot_ext_id="snap1", dry_run=True)
    assert result["dryRun"] is True
    assert result["wouldDelete"]["snapshotExtId"] == "snap1"
    conn.delete.assert_not_called()
