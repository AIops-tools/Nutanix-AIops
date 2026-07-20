"""MCP governed-twin coverage: undo-descriptor callbacks (pure functions) and the
write-tool bodies driven end-to-end through the governance harness.

The undo callbacks are exercised directly with synthetic (params, result) pairs
so every branch — prior ON/OFF, no-op, non-dict — is asserted. The governed
twins are called against a MagicMock connection with the harness bound to a
throwaway home (``NUTANIX_AIOPS_HOME``) so audit/undo rows land on temp disk, not
the developer's real ``~/.nutanix``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import nutanix_aiops.governance.audit as audit_mod
import nutanix_aiops.governance.policy as policy_mod
import nutanix_aiops.governance.undo as undo_mod


def _reset_singletons() -> None:
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()


@pytest.fixture
def gov_home(tmp_path, monkeypatch):
    """Bind the whole harness to a temp home; conftest supplies the approver."""
    monkeypatch.setenv("NUTANIX_AIOPS_HOME", str(tmp_path))
    _reset_singletons()
    yield tmp_path
    _reset_singletons()


# ── undo-descriptor callbacks (pure) ───────────────────────────────────────


@pytest.mark.unit
def test_power_undo_inverts_prior_state():
    from mcp_server.tools import vms as gov

    on = gov._power_undo({"vm_ext_id": "v1"}, {"priorState": {"powerState": "ON"}})
    assert on["tool"] == "vm_power_on" and on["params"]["vm_ext_id"] == "v1"

    off = gov._power_undo({"vm_ext_id": "v1"}, {"priorState": {"powerState": "OFF"}})
    assert off["tool"] == "vm_power_off"

    # unknown prior state and non-dict result → no undo descriptor
    assert gov._power_undo({"vm_ext_id": "v1"}, {"priorState": {"powerState": ""}}) is None
    assert gov._power_undo({"vm_ext_id": "v1"}, "not-a-dict") is None


@pytest.mark.unit
def test_update_and_migrate_undo_callbacks():
    from mcp_server.tools import vms as gov

    upd = gov._update_undo(
        {"vm_ext_id": "v1"}, {"priorState": {"numSockets": 2, "memoryBytes": 4}})
    assert upd["tool"] == "vm_update"
    assert upd["params"] == {"vm_ext_id": "v1", "num_sockets": 2, "memory_bytes": 4}
    # nothing captured → no undo
    assert gov._update_undo(
        {"vm_ext_id": "v1"}, {"priorState": {"numSockets": None, "memoryBytes": None}}) is None

    mig = gov._migrate_undo({"vm_ext_id": "v1"}, {"priorState": {"hostExtId": "h-old"}})
    assert mig["tool"] == "vm_migrate"
    assert mig["params"]["target_host_ext_id"] == "h-old"
    assert gov._migrate_undo({"vm_ext_id": "v1"}, {"priorState": {"hostExtId": ""}}) is None
    assert gov._migrate_undo({"vm_ext_id": "v1"}, None) is None


@pytest.mark.unit
def test_storage_update_undo_callback():
    from mcp_server.tools import storage as gov

    d = gov._update_undo(
        {"ext_id": "sc1"}, {"priorState": {"maxCapacityBytes": 10, "replicationFactor": 2}})
    assert d["tool"] == "storage_container_update"
    assert d["params"] == {"ext_id": "sc1", "max_capacity_bytes": 10, "replication_factor": 2}
    assert gov._update_undo(
        {"ext_id": "sc1"}, {"priorState": {"maxCapacityBytes": None,
                                           "replicationFactor": None}}) is None
    assert gov._update_undo({"ext_id": "sc1"}, "x") is None


# ── governed write bodies through the harness ──────────────────────────────


@pytest.mark.unit
def test_vm_power_on_twin_records_undo_on_disk(gov_home, monkeypatch):
    from mcp_server.tools import vms as gov

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": {"extId": "v1", "name": "web01",
                                                 "powerState": "OFF"}}, "etag-1")
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)

    result = gov.vm_power_on(vm_ext_id="v1")
    assert result["action"] == "power-on"
    assert result.get("_undo_id"), "reversible write must carry an undo id"
    conn.post.assert_called_once()


@pytest.mark.unit
def test_vm_delete_dry_run_previews_without_mutating(gov_home, monkeypatch):
    from mcp_server.tools import vms as gov

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": {"extId": "v1", "name": "web01",
                                                 "powerState": "ON"}}, "etag-1")
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)

    result = gov.vm_delete(vm_ext_id="v1", dry_run=True)
    assert result["dryRun"] is True
    assert result["wouldDelete"]["name"] == "web01"
    conn.delete.assert_not_called()


@pytest.mark.unit
def test_vm_migrate_dry_run_previews_without_mutating(gov_home, monkeypatch):
    from mcp_server.tools import vms as gov

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": {"extId": "v1", "name": "web01",
                                                 "host": {"extId": "h-old"}}}, "etag-1")
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)

    result = gov.vm_migrate(vm_ext_id="v1", target_host_ext_id="h-new", dry_run=True)
    assert result["dryRun"] is True
    assert result["fromHost"] == "h-old"
    assert result["toHost"] == "h-new"
    conn.post.assert_not_called()


@pytest.mark.unit
def test_vm_delete_twin_executes_and_audits(gov_home, monkeypatch):
    from mcp_server.tools import vms as gov

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": {"extId": "v1", "name": "web01",
                                                 "powerState": "ON"}}, "etag-1")
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)

    result = gov.vm_delete(vm_ext_id="v1")
    assert result["action"] == "delete_vm"
    conn.delete.assert_called_once()


@pytest.mark.unit
def test_storage_container_create_and_update_twins(gov_home, monkeypatch):
    from mcp_server.tools import storage as gov

    conn = MagicMock(name="conn")
    conn.post.return_value = {"data": {"extId": "task-1"}}
    conn.get_with_etag.return_value = (
        {"data": {"extId": "sc1", "name": "c", "maxCapacityBytes": 10, "replicationFactor": 2}},
        "etag-1",
    )
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)

    created = gov.storage_container_create(name="sc-new", cluster_ext_id="cl-1")
    assert created["action"] == "create_storage_container"

    updated = gov.storage_container_update(ext_id="sc1", max_capacity_bytes=20)
    assert updated["priorState"]["maxCapacityBytes"] == 10
    assert updated.get("_undo_id"), "reversible update must record an undo id"


@pytest.mark.unit
def test_snapshot_restore_and_pd_failover_dry_runs(gov_home, monkeypatch):
    from mcp_server.tools import dataprotection as gov

    conn = MagicMock(name="conn")
    # The revert preview now READS the VM (one GET) so it can run the same
    # self-lockout check the real revert would — a preview that cannot be
    # refused would promise an operation the write then rejects.
    conn.get_with_etag.return_value = ({"data": {"extId": "v1", "name": "web01"}}, "etag-1")
    conn.target.host = "10.0.0.10"
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)

    restore = gov.snapshot_restore(vm_ext_id="v1", snapshot_ext_id="snap-1", dry_run=True)
    assert restore["wouldRevert"]["snapshotExtId"] == "snap-1"

    failover = gov.pd_failover(policy_ext_id="pol-1", cluster_ext_id="cl-2", dry_run=True)
    assert failover["wouldFailover"]["targetClusterExtId"] == "cl-2"
    conn.post.assert_not_called()  # still a preview: nothing was written


@pytest.mark.unit
def test_image_delete_twin_executes_and_captures_prior(gov_home, monkeypatch):
    from mcp_server.tools import catalog as gov

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": {"extId": "img-1", "name": "ubuntu.iso"}},
                                       "etag-3")
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)

    result = gov.image_delete(ext_id="img-1")
    assert result["priorState"] == {"name": "ubuntu.iso"}
    conn.delete.assert_called_once()


# ── dry_run previews are guarded at the MCP wrapper, not just in ops ─────────


def _pc_mcp_conn(raw, host="10.0.0.10"):
    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": raw}, "etag-1")
    conn.target.host = host
    return conn


_PC_VM = {"extId": "vm-pc", "name": "pc", "powerState": "ON",
          "nics": [{"networkInfo": {"ipv4Config": {"ipAddress": [{"value": "10.0.0.10"}]}}}]}
_WEB_VM = {"extId": "vm-web", "name": "web01", "powerState": "ON",
           "nics": [{"networkInfo": {"ipv4Config": {"ipAddress": [{"value": "10.0.0.55"}]}}}]}


@pytest.mark.unit
def test_vm_delete_dry_run_on_prism_central_is_refused(gov_home, monkeypatch):
    """Pins the WIRING: the wrapper must route its preview through the guarded
    ops.preview_delete_vm. Reverting it to the old get_vm call would still pass
    every ops-level test, so the assertion has to live here."""
    from mcp_server.tools import vms as gov

    conn = _pc_mcp_conn(_PC_VM)
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)

    out = gov.vm_delete(vm_ext_id="vm-pc", dry_run=True)
    assert "Refusing to delete" in out["error"]
    assert "wouldDelete" not in out  # no green preview for a call that will be refused
    conn.delete.assert_not_called()


@pytest.mark.unit
def test_vm_delete_dry_run_on_a_normal_vm_still_returns_its_preview(gov_home, monkeypatch):
    """Proves the dry-run guard is EXACT, not blanket — same Prism Central target."""
    from mcp_server.tools import vms as gov

    conn = _pc_mcp_conn(_WEB_VM)
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)

    out = gov.vm_delete(vm_ext_id="vm-web", dry_run=True)
    assert out["dryRun"] is True
    assert out["wouldDelete"]["name"] == "web01"
    assert out["wouldDelete"]["powerState"] == "ON"
    conn.delete.assert_not_called()


@pytest.mark.unit
def test_snapshot_restore_dry_run_on_prism_central_is_refused(gov_home, monkeypatch):
    from mcp_server.tools import dataprotection as gov

    conn = _pc_mcp_conn(_PC_VM)
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)

    out = gov.snapshot_restore(vm_ext_id="vm-pc", snapshot_ext_id="snap-1", dry_run=True)
    assert "Refusing to revert" in out["error"]
    assert "wouldRevert" not in out
    conn.post.assert_not_called()


@pytest.mark.unit
def test_snapshot_restore_dry_run_on_a_normal_vm_still_returns_its_preview(gov_home, monkeypatch):
    from mcp_server.tools import dataprotection as gov

    conn = _pc_mcp_conn(_WEB_VM)
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)

    out = gov.snapshot_restore(vm_ext_id="vm-web", snapshot_ext_id="snap-1", dry_run=True)
    assert out["dryRun"] is True
    assert out["wouldRevert"] == {"vmExtId": "vm-web", "snapshotExtId": "snap-1"}
    conn.post.assert_not_called()
