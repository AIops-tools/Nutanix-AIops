"""Unit tests for the VM inventory + lifecycle ops module.

No real Prism Central — the connection is a MagicMock. Proves NIC/IP extraction
and the AHV-vs-ESXi hypervisor fold, the ``include_esxi`` filter, that ``get_vm``
surfaces the ETag and raises KeyError on an empty payload, and that every write
sends the fetched ETag as If-Match and captures the right BEFORE-state into
``priorState`` for a faithful undo/audit trail.
"""

from unittest.mock import MagicMock

import pytest

from nutanix_aiops.ops import vms as ops

_VMS = "/api/vmm/v4.0/ahv/config/vms"


def _vm_etag(conn, raw, etag="etag-1"):
    conn.get_with_etag.return_value = ({"data": raw}, etag)
    return conn


@pytest.mark.unit
def test_norm_vm_extracts_nic_ips_and_cluster_host_refs():
    conn = MagicMock(name="conn")
    conn.list_all.return_value = [
        {
            "extId": "vm-1",
            "name": "web01",
            "powerState": "ON",
            "numSockets": 2,
            "numCoresPerSocket": 1,
            "memorySizeBytes": 4294967296,
            "cluster": {"extId": "cl-1"},
            "host": {"extId": "h-1"},
            "nics": [
                {"networkInfo": {"ipv4Config": {"ipAddress": [{"value": "10.0.0.5"}]}}},
                {"networkInfo": {"ipv4Config": {"ipAddress": [{"value": ""}]}}},
            ],
        }
    ]
    (row,) = ops.list_vms(conn)["vms"]
    assert conn.list_all.call_args[0][0] == _VMS
    assert row["extId"] == "vm-1"
    assert row["hypervisor"] == "AHV"  # default when no hypervisorType/source
    assert row["clusterExtId"] == "cl-1"
    assert row["hostExtId"] == "h-1"
    assert row["ipAddresses"] == ["10.0.0.5"]  # blank value dropped


@pytest.mark.unit
def test_list_vms_include_esxi_false_filters_out_esxi_backed_vms():
    conn = MagicMock(name="conn")
    conn.list_all.return_value = [
        {"extId": "vm-ahv", "name": "a", "hypervisorType": "AHV"},
        {"extId": "vm-esxi", "name": "b", "source": {"entityType": "ESXi"}},
    ]
    all_rows = ops.list_vms(conn, include_esxi=True)["vms"]
    assert {r["extId"] for r in all_rows} == {"vm-ahv", "vm-esxi"}

    ahv_only = ops.list_vms(conn, include_esxi=False)["vms"]
    assert [r["extId"] for r in ahv_only] == ["vm-ahv"]
    assert ahv_only[0]["hypervisor"] == "AHV"


@pytest.mark.unit
def test_get_vm_surfaces_etag_and_hits_the_right_endpoint():
    conn = MagicMock(name="conn")
    _vm_etag(conn, {"extId": "vm-1", "name": "web01", "powerState": "OFF"}, "etag-9")
    result = ops.get_vm(conn, "vm-1")
    conn.get_with_etag.assert_called_once_with(f"{_VMS}/vm-1")
    assert result["_etag"] == "etag-9"
    assert result["powerState"] == "OFF"


@pytest.mark.unit
def test_get_vm_raises_keyerror_on_empty_payload():
    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": {}}, "etag-0")
    with pytest.raises(KeyError, match="vm-404"):
        ops.get_vm(conn, "vm-404")


@pytest.mark.unit
def test_power_on_captures_prior_state_and_posts_with_etag():
    conn = MagicMock(name="conn")
    _vm_etag(conn, {"extId": "vm-1", "name": "web01", "powerState": "OFF"}, "etag-5")
    result = ops.power_on(conn, "vm-1")
    assert result["action"] == "power-on"
    assert result["priorState"] == {"powerState": "OFF"}
    conn.post.assert_called_once_with(
        f"{_VMS}/vm-1/$actions/power-on", etag="etag-5", json={}
    )


@pytest.mark.unit
def test_update_vm_merges_changes_over_prior_body_and_records_prior():
    conn = MagicMock(name="conn")
    _vm_etag(
        conn,
        {"extId": "vm-1", "name": "web01", "numSockets": 2, "memorySizeBytes": 4294967296},
        "etag-7",
    )
    result = ops.update_vm(conn, "vm-1", num_sockets=4)
    assert result["priorState"] == {"numSockets": 2, "memoryBytes": 4294967296}
    args, kwargs = conn.put.call_args
    assert args[0] == f"{_VMS}/vm-1"
    assert kwargs["etag"] == "etag-7"
    assert kwargs["json"]["numSockets"] == 4
    # memory left untouched because memory_bytes was not passed
    assert kwargs["json"]["memorySizeBytes"] == 4294967296


@pytest.mark.unit
def test_clone_vm_posts_clone_action_with_new_name():
    conn = MagicMock(name="conn")
    _vm_etag(conn, {"extId": "vm-1", "name": "web01"}, "etag-3")
    conn.post.return_value = {"data": {"extId": "task-1"}}
    result = ops.clone_vm(conn, "vm-1", "web01-copy")
    assert result["action"] == "clone_vm"
    assert result["newName"] == "web01-copy"
    assert result["taskExtId"] == "task-1"
    conn.post.assert_called_once_with(
        f"{_VMS}/vm-1/$actions/clone", etag="etag-3", json={"name": "web01-copy"}
    )


@pytest.mark.unit
def test_delete_vm_captures_prior_name_and_power_state():
    conn = MagicMock(name="conn")
    _vm_etag(conn, {"extId": "vm-1", "name": "web01", "powerState": "ON"}, "etag-2")
    result = ops.delete_vm(conn, "vm-1")
    assert result["name"] == "web01"
    assert result["priorState"] == {"powerState": "ON"}
    conn.delete.assert_called_once_with(f"{_VMS}/vm-1", etag="etag-2")


@pytest.mark.unit
def test_migrate_vm_captures_prior_host_and_targets_new_host():
    conn = MagicMock(name="conn")
    _vm_etag(conn, {"extId": "vm-1", "name": "web01", "host": {"extId": "h-old"}}, "etag-4")
    conn.post.return_value = {"data": {"extId": "task-9"}}
    result = ops.migrate_vm(conn, "vm-1", "h-new")
    assert result["priorState"] == {"hostExtId": "h-old"}
    assert result["targetHostExtId"] == "h-new"
    assert result["taskExtId"] == "task-9"
    args, kwargs = conn.post.call_args
    assert args[0] == f"{_VMS}/vm-1/$actions/migrate"
    assert kwargs["json"] == {"targetHost": {"extId": "h-new"}}


@pytest.mark.unit
def test_create_vm_builds_minimal_spec():
    conn = MagicMock(name="conn")
    conn.post.return_value = {"data": {"extId": "task-1"}}
    result = ops.create_vm(conn, "new-vm", "cl-1", num_sockets=2, memory_bytes=8589934592)
    assert result["action"] == "create_vm"
    assert result["taskExtId"] == "task-1"
    args, kwargs = conn.post.call_args
    assert args[0] == _VMS
    assert kwargs["json"]["name"] == "new-vm"
    assert kwargs["json"]["cluster"] == {"extId": "cl-1"}
    assert kwargs["json"]["numSockets"] == 2
    assert kwargs["json"]["memorySizeBytes"] == 8589934592
