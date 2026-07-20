"""Absent fields come back as null, not as an empty string — and truncation says so.

An empty string reads as "this field exists and is empty"; a missing field is a
different fact. Prism Central v4 omits a great many fields (``description``,
``hypervisorType``, host/cluster names, LCM version strings), so collapsing the
two hides information from every consumer, and a smaller local model will
confidently invent the difference. These tests pin the contract end-to-end:
helper, ops layer, and the CLI rendering that has to cope with a null.

The second half pins the truncation envelope: a capped list result must say it
was capped, with ``truncated`` *measured* (one extra row fetched) rather than
guessed from a length coincidence.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from nutanix_aiops.cli import app
from nutanix_aiops.governance import opt_str
from nutanix_aiops.ops import _util
from nutanix_aiops.ops import clusters as cl
from nutanix_aiops.ops import vms as vm

runner = CliRunner()


# ── opt_str / opt ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_opt_str_distinguishes_absent_from_empty():
    assert opt_str(None) is None, "absent must stay absent"
    assert opt_str("") == "", "a genuinely empty value is not the same as absent"
    assert opt_str("pc-lab", 64) == "pc-lab"


@pytest.mark.unit
def test_opt_str_still_sanitizes_and_truncates():
    assert opt_str("a\x00b") == "ab"  # control character stripped
    # A cut announces itself: the ellipsis is the only signal a reader gets
    # that what they are looking at is not the whole value.
    assert opt_str("abcdef", 3) == "ab\u2026"
    assert opt_str("abc", 3) == "abc"  # exactly at the cap is not truncated


@pytest.mark.unit
def test_opt_str_accepts_non_string_values():
    assert opt_str(42) == "42"


@pytest.mark.unit
def test_util_opt_preserves_none_while_s_collapses_it():
    """``s`` is for always-present values; ``opt`` is for what the API may omit."""
    assert _util.opt(None) is None
    assert _util.s(None) == ""


# ── ops layer: absent != empty ───────────────────────────────────────────


@pytest.mark.unit
def test_ops_report_absent_fields_as_none():
    """A host row with no name/hypervisor/nodeStatus reports null, not ''."""
    conn = MagicMock(name="conn")
    conn.list_all.return_value = [{"extId": "host-1"}]  # everything else absent
    (row,) = cl.list_hosts(conn)["hosts"]
    assert row["extId"] == "host-1"
    assert row["name"] is None
    assert row["hypervisor"] is None
    assert row["nodeStatus"] is None


@pytest.mark.unit
def test_ops_keep_empty_string_when_source_is_empty():
    """An explicitly empty upstream value is preserved as '' — not turned into null."""
    conn = MagicMock(name="conn")
    conn.list_all.return_value = [{"extId": "vm-1", "name": "", "powerState": ""}]
    (row,) = vm.list_vms(conn)["vms"]
    assert row["name"] == ""
    assert row["powerState"] == ""


@pytest.mark.unit
def test_ops_never_drop_the_key_itself():
    """Keys are always present; only their value may be null.

    Omitting a key entirely is worse than a null — the consumer cannot tell the
    field was even considered.
    """
    conn = MagicMock(name="conn")
    conn.list_all.return_value = [{}]
    (row,) = vm.list_vms(conn)["vms"]
    for key in ("extId", "name", "powerState", "hypervisor", "numSockets",
                "memoryBytes", "clusterExtId", "hostExtId", "ipAddresses"):
        assert key in row, f"{key} must be present even when the source omitted it"
    assert row["name"] is None
    assert row["powerState"] is None


@pytest.mark.unit
def test_a_null_power_state_does_not_crash_the_undo_callback():
    """The undo builder reads priorState.powerState — which may now be null."""
    from mcp_server.tools.vms import _power_undo

    assert _power_undo({"vm_ext_id": "vm-1"}, {"priorState": {"powerState": None}}) is None
    undo = _power_undo({"vm_ext_id": "vm-1"}, {"priorState": {"powerState": "ON"}})
    assert undo["tool"] == "vm_power_on"


@pytest.mark.unit
def test_alert_rca_survives_a_null_severity_and_impact():
    """The RCA heuristic reads two optional fields; neither may crash it."""
    from nutanix_aiops.ops.alerts import _probable_cause, _suggested_actions

    assert isinstance(_probable_cause(None, None), str)
    assert _suggested_actions(None, None), "actions must still be produced"


# ── truncation announces itself ──────────────────────────────────────────


@pytest.mark.unit
def test_truncation_is_measured_not_guessed():
    """limit+1 rows are fetched, so ``truncated`` is observed rather than inferred."""
    conn = MagicMock(name="conn")
    conn.list_all.return_value = [{"extId": f"vm-{i}"} for i in range(4)]
    result = vm.list_vms(conn, limit=3)
    assert result["returned"] == 3, "only `limit` rows are returned"
    assert result["limit"] == 3
    assert result["truncated"] is True
    assert conn.list_all.call_args.kwargs["max_items"] == 4, "one extra row is requested"


@pytest.mark.unit
def test_an_exactly_full_page_is_not_reported_as_truncated():
    """The length coincidence a bare list forces a consumer to guess from."""
    conn = MagicMock(name="conn")
    conn.list_all.return_value = [{"extId": f"vm-{i}"} for i in range(3)]
    result = vm.list_vms(conn, limit=3)
    assert result["returned"] == 3
    assert result["truncated"] is False


@pytest.mark.unit
def test_every_list_envelope_carries_the_same_four_keys():
    conn = MagicMock(name="conn")
    conn.list_all.return_value = [{"extId": "x"}]
    for result, key in (
        (vm.list_vms(conn), "vms"),
        (cl.list_clusters(conn), "clusters"),
        (cl.list_hosts(conn), "hosts"),
    ):
        assert set(result) == {key, "returned", "limit", "truncated"}
        assert result["returned"] == len(result[key])


@pytest.mark.unit
def test_limit_is_clamped_to_a_sane_range():
    assert _util.clamp_limit(0) == 1
    assert _util.clamp_limit(-5) == 1
    assert _util.clamp_limit(10**9) == _util.MAX_LIST_LIMIT
    assert _util.clamp_limit("not-a-number") == _util.DEFAULT_LIST_LIMIT


@pytest.mark.unit
def test_undo_list_reports_truncation(monkeypatch):
    """The one pre-existing limit-bearing tool now announces a capped listing."""
    from mcp_server.tools import undo as undo_tools

    rows = [
        {"undo_id": f"u{i}", "ts": "t", "tool": "vm_delete",
         "undo_tool": "vm_create", "note": ""}
        for i in range(3)
    ]
    store = MagicMock()
    store.list.return_value = rows
    monkeypatch.setattr(undo_tools, "get_undo_store", lambda: store)

    result = undo_tools.undo_list(limit=2)
    assert result["returned"] == 2
    assert result["limit"] == 2
    assert result["truncated"] is True
    assert store.list.call_args.kwargs["limit"] == 3, "one extra row is requested"


# ── CLI rendering ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_renders_rows_with_null_fields(monkeypatch):
    """The output must survive a null field rather than crashing on render."""
    import nutanix_aiops.cli.vm as vm_cli

    conn = MagicMock(name="conn")
    # A VM with no name and no power state — both become None at the ops layer.
    conn.list_all.return_value = [{"extId": "vm-1"}]
    monkeypatch.setattr(vm_cli, "get_connection", lambda target=None: (conn, None))

    result = runner.invoke(app, ["vm", "list"])
    assert result.exit_code == 0, result.output
    assert "vm-1" in result.output
    assert "null" in result.output, "an absent field renders as null, not ''"


@pytest.mark.unit
def test_cli_says_out_loud_that_output_was_truncated(monkeypatch):
    """The flag is in the JSON, but a weak model needs it in plain language too."""
    import nutanix_aiops.cli.vm as vm_cli

    conn = MagicMock(name="conn")
    conn.list_all.return_value = [{"extId": f"vm-{i}"} for i in range(3)]
    monkeypatch.setattr(vm_cli, "get_connection", lambda target=None: (conn, None))

    result = runner.invoke(app, ["vm", "list", "--limit", "2"])
    assert result.exit_code == 0, result.output
    assert "truncated" in result.output
    assert "--limit" in result.output, "the user must be told how to see the rest"
