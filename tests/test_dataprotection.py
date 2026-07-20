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


# ── self-lockout guard on snapshot_restore ──────────────────────────────────
#
# A revert is irreversible AND rolls Prism Central's own database back under the
# running service, so it refuses on the PC VM. Exact + fails open, as elsewhere.


def _restore_conn(raw, host="10.0.0.10"):
    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": raw}, "etag-1")
    conn.post.return_value = {"data": {"extId": "task1"}}
    conn.target.host = host
    return conn


def _vm_with_ip(ip):
    return {"extId": "vm-pc", "name": "pc", "powerState": "ON",
            "nics": [{"networkInfo": {"ipv4Config": {"ipAddress": [{"value": ip}]}}}]}


@pytest.mark.unit
def test_restore_snapshot_refuses_the_prism_central_vm():
    from nutanix_aiops.ops import dataprotection as ops
    from nutanix_aiops.ops._selfguard import SelfLockout

    conn = _restore_conn(_vm_with_ip("10.0.0.10"))
    with pytest.raises(SelfLockout) as exc:
        ops.restore_snapshot(conn, "vm-pc", "snap-1")
    assert "10.0.0.10" in str(exc.value)
    conn.post.assert_not_called()  # refused BEFORE the revert


@pytest.mark.unit
def test_restore_snapshot_is_exact_another_vm_still_reverts():
    from nutanix_aiops.ops import dataprotection as ops

    conn = _restore_conn(_vm_with_ip("10.0.0.55"))
    out = ops.restore_snapshot(conn, "vm-web", "snap-1")
    assert out["action"] == "restore_snapshot"
    conn.post.assert_called_once()
    assert conn.post.call_args.args[0].endswith("/$actions/revert")


@pytest.mark.unit
def test_restore_snapshot_fails_open_when_vm_reports_no_nics():
    from nutanix_aiops.ops import dataprotection as ops

    conn = _restore_conn({"extId": "vm-x", "name": "x"})
    out = ops.restore_snapshot(conn, "vm-x", "snap-1")
    assert out["action"] == "restore_snapshot"
    conn.post.assert_called_once()


@pytest.mark.unit
def test_dry_run_restore_refuses_the_prism_central_vm():
    """A revert preview must be refusable — otherwise it promises an operation
    the real call rejects, and a smaller model retries the refusal as if it
    were transient."""
    from nutanix_aiops.ops import dataprotection as ops
    from nutanix_aiops.ops._selfguard import SelfLockout

    conn = _restore_conn(_vm_with_ip("10.0.0.10"))
    with pytest.raises(SelfLockout):
        ops.preview_restore_snapshot(conn, "vm-pc", "snap-1")
    conn.post.assert_not_called()


@pytest.mark.unit
def test_dry_run_restore_is_exact_non_self_target_still_previews():
    from nutanix_aiops.ops import dataprotection as ops

    conn = _restore_conn(_vm_with_ip("10.0.0.55"))
    out = ops.preview_restore_snapshot(conn, "vm-web", "snap-1")
    assert out == {"vmExtId": "vm-web", "snapshotExtId": "snap-1"}
    conn.post.assert_not_called()


@pytest.mark.unit
def test_dry_run_restore_fails_open_exactly_like_the_real_call():
    from nutanix_aiops.ops import dataprotection as ops

    conn = _restore_conn({"extId": "vm-x", "name": "x"})
    assert ops.preview_restore_snapshot(conn, "vm-x", "snap-1")["vmExtId"] == "vm-x"
