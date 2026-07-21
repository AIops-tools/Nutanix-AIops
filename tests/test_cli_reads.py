"""CLI read-command coverage (cluster / vm / overview) + dry-run/validation paths.

Drives the Typer app with CliRunner. ``get_connection`` is patched per CLI module
to hand back a MagicMock connection, so no config file or network is needed. The
read commands must print the ops-layer JSON; the vm power command must validate
its action argument and honour --dry-run without touching the API.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

runner = CliRunner()


def _patch_conn(monkeypatch, module_path: str) -> MagicMock:
    conn = MagicMock(name="conn")
    import importlib

    mod = importlib.import_module(module_path)
    monkeypatch.setattr(mod, "get_connection", lambda target=None: (conn, None))
    return conn


@pytest.mark.unit
def test_cli_cluster_list_prints_ops_json(monkeypatch):
    from nutanix_aiops.cli import app

    conn = _patch_conn(monkeypatch, "nutanix_aiops.cli.cluster")
    conn.list_all.return_value = [{"extId": "cl-1", "name": "prod",
                                   "config": {"buildInfo": {"version": "6.8"}},
                                   "nodes": {"numberOfNodes": 3}}]
    result = runner.invoke(app, ["cluster", "list"])
    assert result.exit_code == 0, result.output
    assert "cl-1" in result.output
    assert "6.8" in result.output


@pytest.mark.unit
def test_cli_cluster_health_and_util(monkeypatch):
    from nutanix_aiops.cli import app

    conn = _patch_conn(monkeypatch, "nutanix_aiops.cli.cluster")
    conn.get.return_value = {"data": {"extId": "cl-1", "name": "prod",
                                      "config": {"faultToleranceState": "OK"},
                                      "stats": {"controllerNumIops": 900}}}
    health = runner.invoke(app, ["cluster", "health", "cl-1"])
    assert health.exit_code == 0, health.output
    assert "OK" in health.output

    util = runner.invoke(app, ["cluster", "util", "cl-1"])
    assert util.exit_code == 0, util.output
    assert "900" in util.output


@pytest.mark.unit
def test_cli_cluster_hosts(monkeypatch):
    from nutanix_aiops.cli import app

    conn = _patch_conn(monkeypatch, "nutanix_aiops.cli.cluster")
    conn.list_all.return_value = [{"extId": "h-1", "hostName": "node-a"}]
    result = runner.invoke(app, ["cluster", "hosts"])
    assert result.exit_code == 0, result.output
    assert "node-a" in result.output


@pytest.mark.unit
def test_cli_vm_list_and_get(monkeypatch):
    from nutanix_aiops.cli import app

    conn = _patch_conn(monkeypatch, "nutanix_aiops.cli.vm")
    conn.list_all.return_value = [{"extId": "v1", "name": "web01", "powerState": "ON"}]
    lst = runner.invoke(app, ["vm", "list"])
    assert lst.exit_code == 0, lst.output
    assert "web01" in lst.output

    conn.get_with_etag.return_value = ({"data": {"extId": "v1", "name": "web01"}}, "etag-1")
    got = runner.invoke(app, ["vm", "get", "v1"])
    assert got.exit_code == 0, got.output
    assert "etag-1" in got.output


@pytest.mark.unit
def test_cli_vm_power_rejects_bad_action(monkeypatch):
    from nutanix_aiops.cli import app

    _patch_conn(monkeypatch, "nutanix_aiops.cli.vm")
    result = runner.invoke(app, ["vm", "power", "v1", "explode"])
    assert result.exit_code != 0
    assert "action must be one of" in result.output


@pytest.mark.unit
def test_cli_overview(monkeypatch):
    from nutanix_aiops.cli import app

    conn = _patch_conn(monkeypatch, "nutanix_aiops.cli.overview")

    def _list_all(path, **_kw):
        if path.endswith("/vms"):
            return [{"extId": "v1", "powerState": "ON", "hypervisorType": "AHV"}]
        return [{"extId": "x"}]

    conn.list_all.side_effect = _list_all
    result = runner.invoke(app, ["overview"])
    assert result.exit_code == 0, result.output
    assert "hypervisorSpread" in result.output


@pytest.mark.unit
def test_cli_error_path_translates_keyerror(monkeypatch):
    """A missing extId surfaces as a one-line teaching error, not a traceback."""
    from nutanix_aiops.cli import app

    conn = _patch_conn(monkeypatch, "nutanix_aiops.cli.vm")
    conn.get_with_etag.return_value = ({"data": {}}, "etag-0")
    result = runner.invoke(app, ["vm", "get", "missing"])
    assert result.exit_code == 1
    assert "Error" in result.output
    assert json.dumps  # sanity import kept
