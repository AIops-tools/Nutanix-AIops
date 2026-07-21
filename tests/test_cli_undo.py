"""CLI undo command coverage: list, apply --dry-run, and confirmed apply.

An inverse ``_undo_cli_probe`` tool is registered on the MCP instance for the
duration of the test, a token is recorded on a real undo.db in an isolated home,
then the ``nutanix-aiops undo`` commands are driven end-to-end with CliRunner.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

import nutanix_aiops.governance.audit as audit_mod
import nutanix_aiops.governance.policy as policy_mod
import nutanix_aiops.governance.undo as undo_mod
from mcp_server._shared import mcp
from nutanix_aiops.governance import governed_tool

runner = CliRunner()
_CALLS: list[dict] = []


@governed_tool(risk_level="low")
def _undo_cli_probe(value: str = "", target=None) -> dict:
    _CALLS.append({"value": value})
    return {"ok": True, "value": value}


@pytest.fixture
def gov_home(tmp_path, monkeypatch):
    _CALLS.clear()
    mcp.add_tool(_undo_cli_probe, name="_undo_cli_probe")
    monkeypatch.setenv("NUTANIX_AIOPS_HOME", str(tmp_path))
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()
    yield tmp_path
    mcp._tool_manager._tools.pop("_undo_cli_probe", None)
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()


def _audit_tools(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute("SELECT tool FROM audit_log ORDER BY id")]
    finally:
        conn.close()


def _record() -> str:
    descriptor = {"tool": "_undo_cli_probe", "params": {"value": "restored"}}
    return undo_mod.get_undo_store().record(
        skill="probe", tool="orig_op", undo_descriptor=descriptor)


@pytest.mark.unit
def test_cli_undo_list_shows_recorded_token(gov_home):
    from nutanix_aiops.cli import app

    uid = _record()
    result = runner.invoke(app, ["undo", "list"])
    assert result.exit_code == 0, result.output
    assert uid in result.output
    assert "_undo_cli_probe" in result.output


@pytest.mark.unit
def test_cli_undo_apply_dry_run_previews_inverse(gov_home):
    """An ordinary preview still renders the banner — and is audited.

    A dry_run MAY read; it must never write. The forbidden half is the inverse
    dispatch (``_CALLS``); the audit row is REQUIRED, since previews have always
    been audited on the MCP path and the CLI was the outlier.
    """
    from nutanix_aiops.cli import app

    uid = _record()
    result = runner.invoke(app, ["undo", "apply", uid, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output
    assert "_undo_cli_probe" in result.output
    assert _CALLS == []  # dry-run never dispatched the inverse
    assert "undo_apply" in _audit_tools(gov_home / "audit.db")
    assert undo_mod.get_undo_store().get(uid)["status"] == "recorded"


@pytest.mark.unit
def test_cli_undo_apply_dry_run_refusal_exits_nonzero(gov_home):
    """Regression: a refused undo preview used to render as a green banner.

    ``undo_apply`` was already being called with dry_run=True here, but its
    refusals ({"error": ...} for an unknown / already-applied token, or an
    unregistered inverse) were read straight through: ``preview.get("wouldApply",
    {})`` quietly yielded {}, so the CLI printed "inverse: ?" and exited 0. A
    weak model reads that as "preview fine, proceed" and retries the apply.
    """
    from nutanix_aiops.cli import app

    result = runner.invoke(app, ["undo", "apply", "deadbeef", "--dry-run"])
    assert result.exit_code == 1
    assert "Unknown undo id" in result.output
    assert "DRY-RUN" not in result.output  # no green banner for a refusal
    assert "inverse: ?" not in result.output  # the old placeholder is gone
    assert _CALLS == []


@pytest.mark.unit
def test_cli_undo_apply_dry_run_already_applied_token_is_refused(gov_home):
    """The other refusal shape: a token can only be applied once."""
    from nutanix_aiops.cli import app

    uid = _record()
    assert runner.invoke(app, ["undo", "apply", uid], input="y\ny\n").exit_code == 0
    _CALLS.clear()

    result = runner.invoke(app, ["undo", "apply", uid, "--dry-run"])
    assert result.exit_code == 1
    # Rich hard-wraps the banner, so match on a fragment that survives wrapping.
    assert "already 'applied'" in " ".join(result.output.split())
    assert "DRY-RUN" not in result.output
    assert _CALLS == []


@pytest.mark.unit
def test_cli_undo_apply_confirmed_dispatches_inverse(gov_home):
    from nutanix_aiops.cli import app

    uid = _record()
    result = runner.invoke(app, ["undo", "apply", uid], input="y\ny\n")
    assert result.exit_code == 0, result.output
    assert _CALLS == [{"value": "restored"}]  # inverse actually ran


@pytest.mark.unit
def test_cli_undo_apply_aborts_without_confirm(gov_home):
    from nutanix_aiops.cli import app

    uid = _record()
    result = runner.invoke(app, ["undo", "apply", uid], input="n\n")
    assert result.exit_code != 0
    assert _CALLS == []
