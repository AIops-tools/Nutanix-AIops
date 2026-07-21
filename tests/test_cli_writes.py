"""CLI confirmed-write path — past dry-run, through governance, onto disk.

The CLI write commands delegate real execution to the ``@governed_tool``
functions in ``mcp_server.tools``. These tests drive a write command PAST the
dry-run branch and the double-confirm prompts and assert the call really went
through the governed path (audit row on disk) — the regression test for the
"CLI writes were unaudited" line-wide fix.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

import nutanix_aiops.governance.audit as audit_mod
import nutanix_aiops.governance.policy as policy_mod
import nutanix_aiops.governance.undo as undo_mod


@pytest.fixture
def gov_home(tmp_path, monkeypatch):
    monkeypatch.setenv("NUTANIX_AIOPS_HOME", str(tmp_path))
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()
    yield tmp_path
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()


def _audit_tools(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute("SELECT tool FROM audit_log ORDER BY id")]
    finally:
        conn.close()


def _mock_vm_conn() -> MagicMock:
    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = (
        {"data": {"extId": "v1", "name": "db01", "powerState": "ON"}},
        "etag-9",
    )
    conn.delete.return_value = {}
    return conn


@pytest.mark.unit
def test_cli_vm_delete_dry_run_reads_and_audits_but_never_writes(gov_home, monkeypatch):
    """A dry_run MAY read; it must never write.

    The older "dry_run does zero I/O" assumption was never a stated rule and is
    wrong on its face: a preview that cannot read cannot answer "would this be
    refused?", which is the most valuable thing a preview can say. So the read
    is expected, the audit row is expected (MCP previews were always audited —
    the CLI silently not auditing was the outlier), and only the MUTATING call
    is forbidden.
    """
    import mcp_server.tools.vms as gov_vms
    from nutanix_aiops.cli import app

    # Harmless audit annotation — the harness does not gate on it.
    monkeypatch.setenv("NUTANIX_AUDIT_APPROVED_BY", "tester")
    conn = _mock_vm_conn()
    monkeypatch.setattr(gov_vms, "_get_connection", lambda target=None: conn)

    result = CliRunner().invoke(app, ["vm", "delete", "v1", "--dry-run"])
    assert result.exit_code == 0
    assert "DRY-RUN" in result.output  # human banner preserved, not raw JSON
    assert "db01" in result.output  # banner filled from the returned dict
    conn.delete.assert_not_called()  # no DELETE
    conn.post.assert_not_called()  # no POST
    conn.get_with_etag.assert_called()  # it DID read, to run the guard
    assert _audit_tools(gov_home / "audit.db") == ["vm_delete"]


@pytest.mark.unit
def test_cli_vm_delete_dry_run_on_prism_central_refuses_nonzero(gov_home, monkeypatch):
    """A refused preview must teach and exit non-zero, like a refused real write."""
    import mcp_server.tools.vms as gov_vms
    from nutanix_aiops.cli import app

    monkeypatch.setenv("NUTANIX_AUDIT_APPROVED_BY", "tester")
    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = (
        {"data": {"extId": "vm-pc", "name": "pc", "powerState": "ON",
                  "nics": [{"networkInfo": {"ipv4Config": {"ipAddress": [
                      {"value": "10.0.0.10"}]}}}]}},
        "etag-1",
    )
    conn.target.host = "10.0.0.10"
    monkeypatch.setattr(gov_vms, "_get_connection", lambda target=None: conn)

    result = CliRunner().invoke(app, ["vm", "delete", "vm-pc", "--dry-run"])
    assert result.exit_code == 1
    assert "Refusing to delete" in result.output
    assert "DRY-RUN" not in result.output  # no green banner for a refusal
    conn.delete.assert_not_called()


@pytest.mark.unit
def test_cli_vm_delete_confirmed_goes_through_governance(gov_home, monkeypatch):
    """Confirmed CLI write must execute via the governed twin: the API call runs
    AND an audit row lands in audit.db (this is what the reroute fix bought)."""
    from nutanix_aiops.cli import app

    conn = _mock_vm_conn()
    import mcp_server.tools.vms as gov_vms

    monkeypatch.setattr(gov_vms, "_get_connection", lambda target=None: conn)
    result = CliRunner().invoke(app, ["vm", "delete", "v1"], input="y\ny\n")
    assert result.exit_code == 0, result.output
    conn.delete.assert_called_once()
    assert _audit_tools(gov_home / "audit.db") == ["vm_delete"]


@pytest.mark.unit
def test_cli_vm_migrate_dry_run_reads_and_audits_but_never_writes(gov_home, monkeypatch):
    """Same invariant as the delete preview, on the newly routed migrate path.

    The banner's host names now come from the twin's own preview (it reads the
    VM to resolve the current host) rather than from a hand-written string that
    could drift from what the migrate would really do.
    """
    import mcp_server.tools.vms as gov_vms
    from nutanix_aiops.cli import app

    monkeypatch.setenv("NUTANIX_AUDIT_APPROVED_BY", "tester")
    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = (
        {"data": {"extId": "v1", "name": "web01", "host": {"extId": "h-old"}}},
        "etag-1",
    )
    monkeypatch.setattr(gov_vms, "_get_connection", lambda target=None: conn)

    result = CliRunner().invoke(app, ["vm", "migrate", "v1", "h-new", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output  # human banner, not raw JSON
    assert "h-new" in result.output  # destination still shown
    assert "h-old" in result.output  # and now the REAL current host, from the twin
    conn.post.assert_not_called()  # no POST — the migrate action never fired
    conn.delete.assert_not_called()
    conn.get_with_etag.assert_called()  # it DID read, to resolve fromHost
    assert _audit_tools(gov_home / "audit.db") == ["vm_migrate"]


@pytest.mark.unit
def test_cli_vm_migrate_dry_run_refusal_exits_nonzero(gov_home, monkeypatch):
    """A preview that the twin refuses must not print a green banner."""
    import mcp_server.tools.vms as gov_vms
    from nutanix_aiops.cli import app
    from nutanix_aiops.connection import NutanixApiError

    monkeypatch.setenv("NUTANIX_AUDIT_APPROVED_BY", "tester")
    conn = MagicMock(name="conn")
    conn.get_with_etag.side_effect = NutanixApiError("VM 'v-gone' does not exist")
    monkeypatch.setattr(gov_vms, "_get_connection", lambda target=None: conn)

    result = CliRunner().invoke(app, ["vm", "migrate", "v-gone", "h-new", "--dry-run"])
    assert result.exit_code == 1
    assert "does not exist" in result.output
    assert "DRY-RUN" not in result.output
    conn.post.assert_not_called()


@pytest.mark.unit
def test_cli_vm_delete_aborts_without_double_confirm(gov_home, monkeypatch):
    from nutanix_aiops.cli import app

    conn = _mock_vm_conn()
    import mcp_server.tools.vms as gov_vms

    monkeypatch.setattr(gov_vms, "_get_connection", lambda target=None: conn)
    result = CliRunner().invoke(app, ["vm", "delete", "v1"], input="y\nn\n")
    assert result.exit_code != 0
    assert conn.method_calls == []
    assert not (gov_home / "audit.db").exists()
