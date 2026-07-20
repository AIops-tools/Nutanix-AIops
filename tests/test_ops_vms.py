"""Unit tests for the VM inventory + lifecycle ops module.

No real Prism Central — the connection is a MagicMock. Proves NIC/IP extraction
and the AHV-vs-ESXi hypervisor fold, the ``include_esxi`` filter, that ``get_vm``
surfaces the ETag and raises KeyError on an empty payload, and that every write
sends the fetched ETag as If-Match and captures the right BEFORE-state into
``priorState`` for a faithful undo/audit trail.
"""

import logging
import threading
import time
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


# ── self-lockout guard: Prism Central is itself a VM in vm_list ─────────────
#
# Powering off / deleting / reverting the Prism Central VM succeeds and removes
# the API that would reverse it. The guard intersects the VM's own NIC addresses
# with the address the target is configured to reach, so it costs no extra call.
# These tests pin that it is EXACT (another VM on the same target still powers
# off) and FAILS OPEN (no NICs / no host / unresolvable host → proceed).

from nutanix_aiops.ops._selfguard import SelfLockout  # noqa: E402


def _pc_conn(raw, host="10.0.0.10", etag="etag-1"):
    """A conn whose target host is ``host`` and whose VM record is ``raw``."""
    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": raw}, etag)
    conn.target.host = host
    return conn


def _vm_with_ip(ip, name="pc"):
    return {"extId": "vm-pc", "name": name, "powerState": "ON",
            "nics": [{"networkInfo": {"ipv4Config": {"ipAddress": [{"value": ip}]}}}]}


@pytest.mark.unit
def test_power_off_refuses_the_prism_central_vm():
    conn = _pc_conn(_vm_with_ip("10.0.0.10"))
    with pytest.raises(SelfLockout) as exc:
        ops.power_off(conn, "vm-pc")
    assert "10.0.0.10" in str(exc.value)
    conn.post.assert_not_called()  # refused BEFORE the mutation


@pytest.mark.unit
def test_guest_shutdown_refuses_the_prism_central_vm():
    conn = _pc_conn(_vm_with_ip("10.0.0.10"))
    with pytest.raises(SelfLockout):
        ops.guest_shutdown(conn, "vm-pc")
    conn.post.assert_not_called()


@pytest.mark.unit
def test_delete_vm_refuses_the_prism_central_vm():
    conn = _pc_conn(_vm_with_ip("10.0.0.10"))
    with pytest.raises(SelfLockout):
        ops.delete_vm(conn, "vm-pc")
    conn.delete.assert_not_called()


@pytest.mark.unit
def test_guard_is_exact_another_vm_on_the_same_target_still_powers_off():
    # Same Prism Central target, a workload VM on a different address.
    conn = _pc_conn(_vm_with_ip("10.0.0.55", name="web01"))
    out = ops.power_off(conn, "vm-web")
    assert out["action"] == "power-off"
    conn.post.assert_called_once()
    assert conn.post.call_args.args[0].endswith("/$actions/power-off")


@pytest.mark.unit
def test_guard_is_exact_prism_central_vm_may_still_be_powered_on():
    # power_on is not destructive and must not be guarded — it is the recovery.
    conn = _pc_conn(_vm_with_ip("10.0.0.10"))
    out = ops.power_on(conn, "vm-pc")
    assert out["action"] == "power-on"
    conn.post.assert_called_once()


@pytest.mark.unit
def test_guard_does_not_touch_clone_or_resize_of_prism_central():
    # Neither destroys the API, so neither is refused (exactness, not blanket).
    conn = _pc_conn(_vm_with_ip("10.0.0.10"))
    conn.post.return_value = {"data": {"extId": "task1"}}
    assert ops.clone_vm(conn, "vm-pc", "copy")["action"] == "clone_vm"
    assert ops.update_vm(conn, "vm-pc", num_sockets=4)["action"] == "update_vm"


@pytest.mark.unit
def test_guard_fails_open_when_vm_reports_no_nics():
    # No IPs must NEVER read as "it is me".
    conn = _pc_conn({"extId": "vm-x", "name": "x", "powerState": "ON"})
    out = ops.power_off(conn, "vm-x")
    assert out["action"] == "power-off"
    conn.post.assert_called_once()


@pytest.mark.unit
def test_guard_fails_open_when_target_has_no_host():
    conn = _pc_conn(_vm_with_ip("10.0.0.10"), host="")
    out = ops.power_off(conn, "vm-pc")
    assert out["action"] == "power-off"
    conn.post.assert_called_once()


@pytest.mark.unit
def test_guard_fails_open_when_target_host_does_not_resolve(monkeypatch):
    # Hermetic: no real DNS. An unresolvable name leaves identity UNKNOWN, and
    # unknown must permit the write rather than block every power-off.
    import nutanix_aiops.ops._selfguard as guard

    def _boom(*_a, **_k):
        raise OSError("Name or service not known")

    monkeypatch.setattr(guard.socket, "getaddrinfo", _boom)
    conn = _pc_conn(_vm_with_ip("10.0.0.10"), host="pc.invalid.example")
    out = ops.power_off(conn, "vm-pc")
    assert out["action"] == "power-off"
    conn.post.assert_called_once()


@pytest.mark.unit
def test_guard_matches_a_hostname_target_that_resolves_to_the_vms_ip(monkeypatch):
    # Configured as a NAME; resolution puts it on the VM's own address.
    import nutanix_aiops.ops._selfguard as guard

    monkeypatch.setattr(
        guard.socket, "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("10.0.0.10", 0))],
    )
    conn = _pc_conn(_vm_with_ip("10.0.0.10"), host="pc.lab.local")
    with pytest.raises(SelfLockout):
        ops.power_off(conn, "vm-pc")
    conn.post.assert_not_called()


@pytest.mark.unit
def test_refusal_messages_survive_the_300_char_cap():
    """The remedy sentence must reach the caller.

    ``mcp_server._shared._safe_error`` passes a ValueError through but sanitizes
    it to 300 characters, and the "use the hypervisor console" instruction is the
    LAST thing in the message. If a cost string grows, the teaching tail is what
    gets cut — silently.
    """
    from nutanix_aiops.ops import dataprotection as dp

    cases = [
        ("power_off", lambda c: ops.power_off(c, "vm-pc")),
        ("guest_shutdown", lambda c: ops.guest_shutdown(c, "vm-pc")),
        ("delete_vm", lambda c: ops.delete_vm(c, "vm-pc")),
        ("restore_snapshot", lambda c: dp.restore_snapshot(c, "vm-pc", "snap-1")),
    ]
    for label, call in cases:
        conn = _pc_conn(_vm_with_ip("10.0.0.10"))
        with pytest.raises(SelfLockout) as exc:
            call(conn)
        msg = str(exc.value)
        assert len(msg) <= 300, f"{label} refusal is {len(msg)} chars; tail will be truncated"
        assert msg.rstrip().endswith("instead."), f"{label} refusal lost its remedy sentence"


# ── dry-run must tell the truth about a refusal ─────────────────────────────
#
# A preview that reports wouldDelete for a call the guard will then refuse is
# the preview being WRONG, not merely incomplete — and it is the weak-model trap
# this line designs against: green preview → refusal → the model reads the
# refusal as transient and retries. Fail-open semantics are identical on both
# paths, so a dry-run can never refuse what the real call would allow.


@pytest.mark.unit
def test_dry_run_delete_refuses_the_prism_central_vm():
    conn = _pc_conn(_vm_with_ip("10.0.0.10"))
    with pytest.raises(SelfLockout):
        ops.preview_delete_vm(conn, "vm-pc")
    conn.delete.assert_not_called()


@pytest.mark.unit
def test_dry_run_delete_is_exact_non_self_target_still_previews():
    conn = _pc_conn(_vm_with_ip("10.0.0.55", name="web01"))
    out = ops.preview_delete_vm(conn, "vm-web")
    assert out["name"] == "web01"
    assert out["powerState"] == "ON"
    conn.delete.assert_not_called()  # a preview writes nothing either way


@pytest.mark.unit
def test_dry_run_delete_fails_open_exactly_like_the_real_call():
    # No NICs → identity unknown → preview proceeds, matching delete_vm.
    conn = _pc_conn({"extId": "vm-x", "name": "x", "powerState": "OFF"})
    assert ops.preview_delete_vm(conn, "vm-x")["name"] == "x"


# ── IPv6 coverage + address canonicalisation ────────────────────────────────


def _vm_with_v6(addr):
    return {"extId": "vm-pc", "name": "pc", "powerState": "ON",
            "nics": [{"networkInfo": {"ipv6Config": {"ipAddress": [{"value": addr}]}}}]}


@pytest.mark.unit
def test_guard_covers_a_prism_central_reached_over_ipv6():
    conn = _pc_conn(_vm_with_v6("2001:db8::1"), host="2001:db8::1")
    with pytest.raises(SelfLockout):
        ops.power_off(conn, "vm-pc")
    conn.post.assert_not_called()


@pytest.mark.unit
def test_guard_matches_ipv6_across_different_textual_spellings():
    # The same address written long-form must still match — raw string equality
    # would miss it, and a missed match is a false "not me".
    conn = _pc_conn(_vm_with_v6("2001:0db8:0000:0000:0000:0000:0000:0001"),
                    host="2001:db8::1")
    with pytest.raises(SelfLockout):
        ops.power_off(conn, "vm-pc")


@pytest.mark.unit
def test_guard_is_exact_a_different_ipv6_vm_still_powers_off():
    conn = _pc_conn(_vm_with_v6("2001:db8::99"), host="2001:db8::1")
    assert ops.power_off(conn, "vm-other")["action"] == "power-off"
    conn.post.assert_called_once()


@pytest.mark.unit
def test_ipv6_addresses_stay_out_of_the_inventory_payload():
    # vm_ips defaults to IPv4-only so the public ipAddresses field is unchanged;
    # only the guard opts into both families.
    from nutanix_aiops.ops._selfguard import vm_ips

    raw = {"nics": [{"networkInfo": {
        "ipv4Config": {"ipAddress": [{"value": "10.0.0.5"}]},
        "ipv6Config": {"ipAddress": [{"value": "2001:db8::1"}]},
    }}]}
    assert vm_ips(raw) == ["10.0.0.5"]
    assert vm_ips(raw, include_ipv6=True) == ["10.0.0.5", "2001:db8::1"]


@pytest.mark.unit
def test_canonical_addr_rejects_non_addresses_and_strips_zone_ids():
    from nutanix_aiops.ops._selfguard import canonical_addr

    assert canonical_addr("fe80::1%eth0") == "fe80::1"
    assert canonical_addr("not-an-ip") == ""
    assert canonical_addr("") == ""
    assert canonical_addr(None) == ""


# ── DNS resolution is bounded and fails open on timeout ─────────────────────


@pytest.mark.unit
def test_resolution_timeout_fails_open_and_warns(monkeypatch, caplog):
    """A black-holed resolver must not stall a power-off.

    getaddrinfo takes no timeout and ignores setdefaulttimeout, so the lookup is
    run on a daemon thread with a deadline. Blowing the deadline permits the
    write (identity unknown) but MUST log — a guard that silently stopped
    guarding would be worse than one that never existed.
    """
    import nutanix_aiops.ops._selfguard as guard

    started = threading.Event()

    def _hang(*_a, **_k):
        started.set()
        time.sleep(30)  # never returns within the deadline

    monkeypatch.setattr(guard.socket, "getaddrinfo", _hang)
    monkeypatch.setattr(guard, "_RESOLVE_TIMEOUT_SEC", 0.05)

    conn = _pc_conn(_vm_with_ip("10.0.0.10"), host="blackhole.lab.local")
    with caplog.at_level(logging.WARNING):
        out = ops.power_off(conn, "vm-pc")

    assert out["action"] == "power-off"  # failed OPEN
    assert started.is_set()
    assert any("exceeded" in r.getMessage() for r in caplog.records)


@pytest.mark.unit
def test_resolution_timeout_does_not_block_the_caller(monkeypatch):
    """The deadline is real wall-clock, not just a flag."""
    import nutanix_aiops.ops._selfguard as guard

    monkeypatch.setattr(guard.socket, "getaddrinfo", lambda *a, **k: time.sleep(30))
    monkeypatch.setattr(guard, "_RESOLVE_TIMEOUT_SEC", 0.05)

    started = time.monotonic()
    assert guard.target_addresses(_pc_conn({}, host="blackhole.lab.local")) == set()
    assert time.monotonic() - started < 5, "resolution was not actually bounded"
