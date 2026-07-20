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

    # vm_delete is risk=high, so the secure-by-default approver gate applies to
    # the preview too now that it runs through the governed twin.
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
def test_cli_high_risk_without_approver_teaches_instead_of_tracebacking(gov_home, monkeypatch):
    """PolicyDenied must render as one teaching line, not a bare traceback.

    Its message names the exact env var to set — the most actionable error this
    tool produces — and it was being swallowed because PolicyDenied is not a
    ValueError.

    Exercised on the REAL write path: a preview deliberately does not demand an
    approver (you would need the approval to learn whether one is needed), so
    the denial only happens here.
    """
    import mcp_server.tools.vms as gov_vms
    from nutanix_aiops.cli import app

    monkeypatch.delenv("NUTANIX_AUDIT_APPROVED_BY", raising=False)
    conn = _mock_vm_conn()
    monkeypatch.setattr(gov_vms, "_get_connection", lambda target=None: conn)

    result = CliRunner().invoke(app, ["vm", "delete", "v1"], input="y\ny\n")
    assert result.exit_code == 1
    assert "NUTANIX_AUDIT_APPROVED_BY" in result.output
    assert result.output.strip(), "a denial must never exit silently"
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
def test_cli_vm_delete_aborts_without_double_confirm(gov_home, monkeypatch):
    from nutanix_aiops.cli import app

    conn = _mock_vm_conn()
    import mcp_server.tools.vms as gov_vms

    monkeypatch.setattr(gov_vms, "_get_connection", lambda target=None: conn)
    result = CliRunner().invoke(app, ["vm", "delete", "v1"], input="y\nn\n")
    assert result.exit_code != 0
    assert conn.method_calls == []
    assert not (gov_home / "audit.db").exists()
